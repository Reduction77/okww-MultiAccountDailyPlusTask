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
- 运行期间临时暂停「自动登录」触发任务（仅内存、不写配置，结束后自动恢复），
  消除登录阶段它与任务自身的登录等待并发点击「进入游戏」造成的窗口闪烁；
- 第二阶段切号失败会自动恢复到登录界面重试，连续失败 3 次才中止任务。
"""

from ok import CannotFindException, TaskDisabledException
from src.task.AutoLoginTask import AutoLoginTask
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
        self._auto_login_task = None

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()
        # dict 保序（Python 3.7+），键的遍历顺序即配置里的输入顺序
        overrides = self._parse_account_tacet_overrides()
        self._suspend_auto_login()
        try:
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
        finally:
            self._restore_auto_login()

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

    def _suspend_auto_login(self):
        """运行期间临时暂停「自动登录」触发任务（仅内存、不写配置，finally 中恢复）。

        自动登录每 5 秒触发一次 wait_login，而本任务在登录/读条阶段也会通过
        ensure_main -> wait_login 点击「进入游戏」；两边并发点击会让登录动画
        反复重启、游戏窗口反复抢焦点，表现为登录时一直闪。
        故意用 _enabled 直接赋值绕过 disable()：disable() 会把 _enabled=False
        写进持久化配置，异常退出后用户的自动登录就再也回不来了。
        """
        task = self.executor.get_task_by_class(AutoLoginTask)
        if task is not None and task.enabled:
            task._enabled = False
            self._auto_login_task = task
            self.log_info('Auto Login trigger paused during this run 运行期间已暂停自动登录触发')

    def _restore_auto_login(self):
        if self._auto_login_task is not None:
            self._auto_login_task._enabled = True
            self._auto_login_task = None
            self.log_info('Auto Login trigger restored 已恢复自动登录触发')

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
