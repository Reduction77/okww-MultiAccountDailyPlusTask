# -*- coding: utf-8 -*-
"""
自定义任务：多账号自动周常乐园（每个账号自动刷满 6000 乐园积分）

本文件放在 ok_tasks/ 目录下即可被 ok-ww 自动加载，不修改任何官方源码，
上游更新（git pull / 覆盖安装）后无需重做。

要求：ok-ww 版本不早于 2026-05（v3.5+，即官方「多账号每日任务」重构后的版本）。

行为说明：
- 从当前已登录的账号开始，依次切换登录界面下拉列表里的所有账号；
- 每个账号运行官方「自动周常乐园」(GardenTask)，该任务自带完成检测：
  已达 6000 积分上限的账号会自动跳过，因此无需识别或配置具体账号；
- 单个账号失败时会截图、恢复主界面并继续下一个账号，不中断整体流程；
- 所有账号跑完后正常结束，停在登录界面。

v1.1 稳定性修复：
- 「返回登录界面」重写为带状态校验的重试流程：esc 未生效会自动重发，
  确认弹窗改为真实等待（上游 click_confirm 写死 1 秒，弹窗动画稍慢就会漏点），
  单次输入丢失不再导致卡死或异常退出；
- 切号流程失败会自动恢复到登录界面重试，连续失败 3 次才中止任务。

v1.2 修复登录时画面一直闪：
- 根因：ok-script 执行器是单线程的，一次性任务运行期间触发任务（自动登录）
  根本不会被调度，登录点击全部来自本任务 ensure_main -> wait_login 的逐帧轮询，
  读条期间「进入游戏」被每秒连点、动画反复重启，画面就一直闪。
- 修复：给 wait_login 加 5 秒防抖（与官方自动登录同节奏），防抖窗口内只做
  状态检测不点击，窗口外才执行官方完整登录逻辑——等效于"让官方来点"。
"""

import time

from ok import CannotFindException, TaskDisabledException
from src.task.GardenTask import GardenTask
from src.task.MultiAccountDailyTask import MultiAccountDailyTask
from src.task.WWOneTimeTask import WWOneTimeTask

CONFIRM_BUTTONS = ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter']
LOGIN_CLICK_DEBOUNCE = 5  # 登录点击防抖秒数，与官方 AutoLoginTask 的触发间隔一致


class MultiAccountGardenTask(MultiAccountDailyTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🎡 Multi Account Garden 多账号周常乐园"
        self.description = ("Automatically switch accounts and run Weekly Garden for each account. "
                            "自动切换账号，为每个账号刷满周常乐园（已满 6000 分的自动跳过）。")
        self._login_click_debounce = 0

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()
        # 首个账号：当前已登录的账号，直接开刷
        self._run_garden()
        self._switch_to_login_safely()
        self._mark_done(self._detect_current_account_from_login())
        self.info_set('Completed', self.done_set)

        # 之后逐个切换剩余账号，直到下拉列表里没有未完成的账号
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
                self.screenshot('MultiAccountGardenTask_switch')
                if consecutive_failures >= 3:
                    raise
                self._recover_to_login()
                continue
            if not next_account:
                break
            consecutive_failures = 0
            self.info_set('Completed', self.done_set)
            self._run_garden()
            self._mark_done(next_account)
            self._switch_to_login_safely()

    def _run_garden(self):
        """为当前已登录的账号运行周常乐园。

        GardenTask.run() 内部会先检查是否已达 6000 积分上限，已满则直接返回，
        所以这里不需要识别账号或做任何前置判断。
        失败处理参照上游 DailyTask.check_weekly_garden：截图、恢复主界面，
        让外层流程继续下一个账号；用户手动停止（TaskDisabledException）必须继续抛出。
        """
        try:
            self.run_task_by_class(GardenTask)
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error('GardenTask Failed', e)
            self.screenshot('MultiAccountGardenTask')
            self.ensure_main(time_out=180)

    # ---------- 以下为稳定性增强 ----------

    def wait_login(self):
        """登录/读条阶段的点击防抖：让点击按官方「自动登录」的节奏来（5 秒一次）。

        背景：ok-script 的执行器是单线程的，一次性任务运行期间触发任务不会被调度，
        所以登录阶段的点击实际全部来自本任务的 ensure_main -> wait_login 轮询——
        它每一帧都会执行，读条期间「进入游戏」横幅一直在屏幕上，就会被每秒连点，
        按钮动画反复重启，表现为画面一直闪；而官方 AutoLoginTask 是 5 秒才触发一次，
        不会闪。这里给 wait_login 加 5 秒防抖：防抖窗口内只做登录状态检测、不点击，
        窗口外才执行官方完整逻辑（含点击），效果等同于"让官方来点"。
        """
        now = time.time()
        if now - self._login_click_debounce < LOGIN_CLICK_DEBOUNCE:
            if self.in_team_and_world():
                self.logged_in = True
                return True
            return False
        self._login_click_debounce = now
        return super().wait_login()

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
