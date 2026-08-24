# -*- coding: utf-8 -*-
"""
自定义任务：多账号每日+（每个账号可自选刷第几个无音区，按输入顺序执行）

本文件放在 ok_tasks/ 目录下即可被 ok-ww 自动加载，不修改任何官方源码，
上游更新（git pull / 覆盖安装）后无需重做。

要求：ok-ww 版本不早于 2026-05（v3.5+，即官方「多账号每日任务」重构后的版本）。

用法：在任务配置的「Per-Account Tacet 每账号无音区」里每行添加一条：
    打码账号=无音区编号
例如：159****19oo=3
账号名以登录界面显示的打码账号为准（含 4 个星号）；
0/o、大小写、.con/.com 差异会自动容错。

执行顺序：
- 配置了自选无音区的账号，严格按照配置里的输入顺序依次执行；
- 未配置的账号随后按登录界面下拉列表的顺序执行，仍使用「每日任务」的全局设置；
- 整个列表留空时与官方多账号任务行为一致。

注意：编号覆盖只在「每日任务」的「Which to Farm」设为 Tacet Suppression（无音区）
时才会生效；若每日任务设的是凝素领域/模拟领域，覆盖值会被忽略。

v1.1 稳定性修复：
- 「返回登录界面」重写为带状态校验的重试流程：esc 未生效会自动重发，
  确认弹窗改为真实等待（上游 click_confirm 写死 1 秒，弹窗动画稍慢就会漏点），
  单次输入丢失不再导致卡死或异常退出；
- 第二阶段切号失败会自动恢复到登录界面重试，连续失败 3 次才中止任务。

v1.2 修复登录时画面一直闪：
- 根因：ok-script 执行器是单线程的，一次性任务运行期间触发任务（自动登录）
  根本不会被调度，登录点击全部来自本任务 ensure_main -> wait_login 的逐帧轮询，
  读条期间「进入游戏」被每秒连点、动画反复重启，画面就一直闪。
- 修复：给 wait_login 加 5 秒防抖（与官方自动登录同节奏），防抖窗口内只做
  状态检测不点击，窗口外才执行官方完整登录逻辑——等效于"让官方来点"。

v1.3 修复前台点不上登录：
- 根因：鸣潮窗口抢到前台后走 RawInput，会无视 PostMessage 后台点击/按键，
  而游戏在退出登录、进入登录界面等切换时会自己抢前台，于是点击全部失效，
  表现为「前台点不上登录，手动聚焦别的窗口才能继续」。
- 修复：检测到游戏在前台时自动把前台焦点让给其他窗口（画面不动，只是失去
  激活状态），后台点击随即恢复；返回登录界面、选号登录、登录等待、
  每日开始时都会检查。
"""

import time

from ok import CannotFindException, TaskDisabledException
from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.DailyTask import DailyTask
from src.task.MouseResetTask import MouseResetTask
from src.task.MultiAccountDailyTask import (MultiAccountDailyTask, account_pattern,
                                            normalize_account_name)
from src.task.TacetTask import TacetTask
from src.task.WWOneTimeTask import WWOneTimeTask

PER_ACCOUNT_TACET = 'Per-Account Tacet 每账号无音区'
TACET_INDEX_KEY = 'Which Tacet Suppression to Farm'  # 必须与 DailyTask 的配置键保持一致
CONFIRM_BUTTONS = ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter']
LOGIN_CLICK_DEBOUNCE = 5  # 登录点击防抖秒数，与官方 AutoLoginTask 的触发间隔一致


class MultiAccountDailyPlusTask(MultiAccountDailyTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "👥 Multi Account Daily+ 多账号每日+"
        self.description = ("Automatically switch accounts and run Daily Task for each account, "
                            "with per-account Tacet Suppression selection, in config order. "
                            "多账号一条龙加强版：每个账号可自选刷第几个无音区，按输入顺序执行。")
        self.default_config[PER_ACCOUNT_TACET] = []
        self.config_description[PER_ACCOUNT_TACET] = (
            '可选。每行一条：打码账号=无音区编号，例如 159****19oo=3。'
            '配置了的账号会严格按照输入顺序先执行，并覆盖「每日任务」的「刷第几个无音区」设置；'
            '未配置的账号随后按登录列表顺序执行，仍用每日任务的设置。'
            '注意：仅当每日任务的「Which to Farm」设为 Tacet Suppression（无音区）时覆盖才生效。 '
            'Optional: masked_account=tacet_number, one per line, e.g. 159****19oo=3. '
            'Configured accounts run first in input order. '
            'Only takes effect when Daily Task farms Tacet Suppression.'
        )
        self._login_click_debounce = 0

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()
        # dict 保序（Python 3.7+），键的遍历顺序即配置里的输入顺序
        overrides = self._parse_account_tacet_overrides()
        if overrides:
            # 第一阶段：配置了自选无音区的账号，严格按输入顺序执行
            self._switch_to_login_safely()
            for target in overrides:
                account = self._select_specific_and_login_account(target)
                if account is None:
                    # 下拉列表里找不到该账号（打码名填错或账号已被移除），跳过
                    continue
                self.info_set('Completed', self.done_set)
                self._run_daily_for_account(account, overrides)
                self._mark_done(account)
                self._switch_to_login_safely()
            self.info_set('Completed', self.done_set)
        else:
            # 无配置时与官方多账号任务一致：先跑当前已登录的账号
            self.run_task_by_class(DailyTask)
            self._switch_to_login_safely()
            self._mark_done(self._detect_current_account_from_login())
            self.info_set('Completed', self.done_set)

        # 第二阶段：剩余未配置的账号，按登录下拉列表顺序执行（官方逻辑 + 失败自愈）
        consecutive_failures = 0
        while True:
            try:
                next_account = self._select_and_login_account()
            except CannotFindException:
                # 所有账号均已完成（官方多账号任务此处直接抛出，这里接住并正常结束）
                self.log_info(self.tr('All accounts completed'))
                break
            except TaskDisabledException:
                raise
            except Exception as e:
                # 切号流程失败（输入丢失、弹窗干扰等）：恢复到登录界面后重试
                consecutive_failures += 1
                self.log_error('Switch account failed 切号失败 ({}/3)'.format(consecutive_failures), e)
                self.screenshot('MultiAccountDailyPlusTask_switch')
                if consecutive_failures >= 3:
                    raise
                self._recover_to_login()
                continue
            if not next_account:
                break
            consecutive_failures = 0
            self.info_set('Completed', self.done_set)
            self._run_daily_for_account(next_account, overrides)
            self._mark_done(next_account)
            self._switch_to_login_safely()
            pass

    def validate_config(self, key, value):
        if key == PER_ACCOUNT_TACET:
            for entry in value or []:
                if self._parse_tacet_entry(entry) is None:
                    return ('无效条目 Invalid entry: {entry}。'
                            '格式 Format: 打码账号=编号, e.g. 159****19oo=3').format(entry=entry)
        return None

    def _max_tacet_index(self):
        try:
            tacet_task = self.get_task_by_class(TacetTask)
            if tacet_task:
                return tacet_task.total_number
        except Exception:
            pass
        return None

    def _parse_tacet_entry(self, entry):
        if not entry or '=' not in str(entry):
            return None
        account, _, index_text = str(entry).partition('=')
        account = normalize_account_name(account.strip())
        try:
            index = int(index_text.strip())
        except ValueError:
            return None
        max_index = self._max_tacet_index()
        if not account or index < 1 or (max_index is not None and index > max_index):
            return None
        return account, index

    def _parse_account_tacet_overrides(self):
        overrides = {}
        for entry in self.config.get(PER_ACCOUNT_TACET) or []:
            parsed = self._parse_tacet_entry(entry)
            if parsed is None:
                self.log_warning('Ignoring invalid per-account tacet entry 忽略无效条目: {}'.format(entry))
                continue
            account, index = parsed
            overrides[account] = index
        if overrides:
            self.log_info('Per-account tacet overrides 每账号无音区（按输入顺序）: {}'.format(overrides))
        return overrides

    def _run_daily_for_account(self, account, overrides):
        self._yield_foreground_if_game_front()
        index = overrides.get(normalize_account_name(account)) if account else None
        if index is None:
            self.run_task_by_class(DailyTask)
            return
        daily_task = self.get_task_by_class(DailyTask)
        if TACET_INDEX_KEY not in daily_task.config:
            self.log_warning(
                '每日任务中找不到配置键「{}」，无音区编号覆盖未生效，可能是 ok-ww 更新后配置键改名，'
                '请检查脚本适配；本次将使用每日任务的全局设置。'
                "Tacet override key '{}' not found in Daily Task config, falling back to global setting.".format(
                    TACET_INDEX_KEY, TACET_INDEX_KEY))
            self.run_task_by_class(DailyTask)
            return
        self.log_info('Account {} farms Tacet Suppression #{} 该账号刷第 {} 个无音区'.format(account, index, index))
        old_value = daily_task.config.get(TACET_INDEX_KEY)
        # 只做内存级覆盖，绕过 Config.__setitem__，不写入每日任务的持久化配置
        dict.__setitem__(daily_task.config, TACET_INDEX_KEY, index)
        try:
            self.run_task_by_class(DailyTask)
        finally:
            dict.__setitem__(daily_task.config, TACET_INDEX_KEY, old_value)

    def _select_specific_and_login_account(self, target):
        """在登录界面下拉列表中选中指定账号并登录，返回登录后的账号名；找不到该账号返回 None。

        流程对齐上游 _select_and_login_account：禁用鼠标重置、下拉重试、选后核对、点击登录。
        区别在于定点选择：_click_target_account_in_list 只点击目标账号，找不到不点任何账号，
        因此不会像官方逻辑那样“顺手选中第一个未完成账号”。
        """
        self._yield_foreground_if_game_front()
        mouse_reset_task = self.executor.get_task_by_class(MouseResetTask)
        mouse_reset_was_enabled = mouse_reset_task.enabled if mouse_reset_task else False
        if mouse_reset_was_enabled:
            mouse_reset_task.disable()
        try:
            max_retries = 5
            current_account = None
            for attempt in range(1, max_retries + 1):
                self.sleep(1)
                drop_down = self.find_account_drop_down()
                if drop_down:
                    self.click(drop_down, after_sleep=2)
                if self.do_find_account_drop_down():
                    self.log_error('click drop down no effect')
                    self.screenshot('multi')
                    continue
                account = self.wait_until(
                    lambda: self._click_target_account_in_list(target),
                    time_out=10, raise_if_not_found=False)
                if not account:
                    self.log_warning(
                        'Account not found in drop down 下拉列表中找不到账号，已跳过: {}'.format(target))
                    if drop_down:
                        # 收起展开的下拉列表，避免影响后续账号的选择
                        self.click(drop_down, after_sleep=1)
                    return None
                self.sleep(1)
                current_account = self._detect_current_account_from_login()
                self.log_info(self.tr('Selected account: {selected}, displayed account: {displayed}').format(
                    selected=account, displayed=current_account))
                if self._same_account(account, current_account):
                    self.log_info(self.tr('Confirmed selected account: {account}').format(account=account))
                    break
                if attempt < max_retries:
                    self.log_info(self.tr('Account display does not match, retrying ({attempt}/{max_retries})').format(
                        attempt=attempt, max_retries=max_retries))
                else:
                    raise Exception(self.tr('Failed to switch account'))
            self.sleep(4)
            texts = self.ocr()
            login_btn = self.find_boxes(texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.8),
                                        match=LOGIN_TEXTS)
            if login_btn:
                self.click(login_btn, after_sleep=3)
            else:
                self.click_relative(0.5, 0.568, hcenter=True, vcenter=True, after_sleep=3)
            self.logged_in = False
            self.ensure_main(time_out=180)
            self.log_info(self.tr('Login successful'))
            return current_account
        finally:
            if mouse_reset_was_enabled:
                mouse_reset_task.enable()

    def _click_target_account_in_list(self, target):
        """在展开的下拉列表中点击指定账号；找不到返回 None（不点击任何账号）。"""
        accounts = self.ocr(match=account_pattern)
        for account in accounts:
            self.all_accounts.add(normalize_account_name(account.name))
            self.info_set('All Accounts', self.all_accounts)
            if self._same_account(account.name, target):
                self.click(account, after_sleep=2)
                return account.name
        return None

    # ---------- 以下为稳定性增强（与多账号周常乐园 v1.1 相同） ----------

    def wait_login(self):
        """登录/读条阶段的点击防抖：让点击按官方「自动登录」的节奏来（5 秒一次）。

        背景：ok-script 的执行器是单线程的，一次性任务运行期间触发任务不会被调度，
        所以登录阶段的点击实际全部来自本任务的 ensure_main -> wait_login 轮询——
        它每一帧都会执行，读条期间「进入游戏」横幅一直在屏幕上，就会被每秒连点，
        按钮动画反复重启，表现为画面一直闪；而官方 AutoLoginTask 是 5 秒才触发一次，
        不会闪。这里给 wait_login 加 5 秒防抖：防抖窗口内只做登录状态检测、不点击，
        窗口外才执行官方完整逻辑（含点击），效果等同于"让官方来点"。
        """
        self._yield_foreground_if_game_front()
        now = time.time()
        if now - self._login_click_debounce < LOGIN_CLICK_DEBOUNCE:
            if self.in_team_and_world():
                self.logged_in = True
                return True
            return False
        self._login_click_debounce = now
        return super().wait_login()

    def _yield_foreground_if_game_front(self):
        """游戏抢到前台时，PostMessage 后台点击/按键会被游戏忽略（前台走 RawInput），
        表现为「登录点不上、要手动聚焦别的窗口才能继续」。检测到游戏在前台时，
        自动把前台焦点让给其他窗口——游戏画面保持不动，只是失去激活状态，
        后台点击随即恢复生效。游戏不在前台时本方法什么都不做。"""
        hwnd_window = self.hwnd
        if hwnd_window is None or not getattr(hwnd_window, 'hwnd', 0):
            return
        try:
            if not hwnd_window.is_foreground():
                return
        except Exception:
            return
        try:
            import win32gui
        except ImportError:
            return
        candidates = []
        try:
            shell_hwnd = win32gui.GetShellWindow()
            if shell_hwnd:
                candidates.append(shell_hwnd)
        except Exception:
            pass
        ok_main = self._get_ok_main_hwnd()
        if ok_main:
            candidates.append(ok_main)
        game_hwnds = {hwnd_window.hwnd}
        try:
            game_hwnds.update(w[0] for w in (hwnd_window.hwnds or []))
        except Exception:
            pass
        for target in candidates:
            if not target or target in game_hwnds:
                continue
            try:
                if self._force_foreground(target, game_hwnds):
                    self.log_info(
                        'Game grabbed foreground, yielded focus 游戏抢到前台，已让出焦点以恢复后台点击')
                    return
            except Exception as e:
                self.log_warning('Yield foreground failed 让出前台焦点失败: {}'.format(e))

    def _force_foreground(self, target, game_hwnds):
        """把前台焦点设给 target 窗口，返回游戏是否已不再处于前台。

        用 AttachThreadInput 把本线程挂到当前前台线程上，绕过 Windows 前台锁；
        失败则用 alt 键技巧临时解除前台锁再试一次。
        """
        import win32api
        import win32con
        import win32gui
        import win32process
        fg = win32gui.GetForegroundWindow()
        if not fg or fg == target:
            return True
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg)
        cur_thread = win32api.GetCurrentThreadId()
        attached = False
        try:
            if fg_thread and fg_thread != cur_thread:
                attached = bool(win32process.AttachThreadInput(cur_thread, fg_thread, True))
            win32gui.BringWindowToTop(target)
            win32gui.SetForegroundWindow(target)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(cur_thread, fg_thread, False)
                except Exception:
                    pass
        if win32gui.GetForegroundWindow() not in game_hwnds:
            return True
        # 兜底：alt 键技巧临时解除前台锁再试一次
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(target)
        except Exception:
            pass
        return win32gui.GetForegroundWindow() not in game_hwnds

    def _get_ok_main_hwnd(self):
        """取 ok-ww 主窗口句柄，作为让出前台焦点时的备选目标。"""
        try:
            from ok import og
            main_window = getattr(og, 'main_window', None)
            if main_window is not None:
                return int(main_window.winId())
        except Exception:
            pass
        return 0

    def _switch_to_login_safely(self):
        """返回登录界面（含回到主界面 + 带校验的切换，失败自动重试，最多 3 次）。

        上游 _switch_to_login 是单次盲操作：esc 只发一次且不校验菜单是否真的打开、
        确认弹窗写死等 1 秒、最后等 60 秒不到就直接抛异常，任何一次输入丢失都会
        表现为卡死或任务报错退出。这里改为可自愈的流程。
        """
        last_error = None
        for attempt in range(1, 4):
            try:
                self.ensure_main(time_out=60)
                self._do_switch_to_login()
                return
            except TaskDisabledException:
                raise
            except Exception as e:
                last_error = e
                self.log_warning(
                    'Switch to login attempt {}/3 failed 返回登录界面第 {}/3 次失败: {}'.format(
                        attempt, attempt, e))
                self.screenshot('switch_to_login_{}'.format(attempt))
        raise Exception('Failed to switch back to login screen 无法返回登录界面') from last_error

    def _do_switch_to_login(self):
        """执行一次「主界面 -> 登录界面」切换，每一步都校验结果，失败即抛异常交给上层重试。"""
        self.log_info(self.tr('Switching back to login screen'))
        self._yield_foreground_if_game_front()
        # 1. 打开终端菜单：esc 未生效就重发，最多 3 次；期间若发现已在登录界面则直接完成
        menu_open = False
        for _ in range(3):
            self.send_key('esc', after_sleep=1.5)
            if self.wait_feature('esc_setting', time_out=5, raise_if_not_found=False):
                menu_open = True
                break
            if self.do_find_account_drop_down():
                self.log_info(self.tr('Back at login screen'))
                return
        if not menu_open:
            raise Exception('Open esc menu failed 终端菜单未能打开')
        # 2. 点击左下角「返回登录」
        self.click_relative(0.04, 0.96, after_sleep=1)
        # 3. 确认弹窗：真实等待最多 5 秒（上游 click_confirm 写死 1 秒，弹窗动画慢时会漏点）
        self.wait_click_feature(CONFIRM_BUTTONS, relative_x=-1, raise_if_not_found=False,
                                threshold=0.6, time_out=5)
        # 4. 等待登录界面出现（账号下拉框 + 登录按钮）
        if self.wait_until(self.do_find_account_drop_down, time_out=30, settle_time=2,
                           raise_if_not_found=False):
            self.log_info(self.tr('Back at login screen'))
            return
        raise Exception('Back to login screen timeout 等待登录界面超时')

    def _recover_to_login(self):
        """切号失败后恢复到登录界面，供下一轮重试；异常吞掉，由外层连续失败计数控制。"""
        try:
            if self.do_find_account_drop_down():
                return
            self.ensure_main(time_out=60)
            self._do_switch_to_login()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_warning('Recover to login failed 恢复登录界面失败: {}'.format(e))
