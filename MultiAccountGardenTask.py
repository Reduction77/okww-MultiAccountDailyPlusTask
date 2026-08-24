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
"""

from ok import CannotFindException, TaskDisabledException
from src.task.GardenTask import GardenTask
from src.task.MultiAccountDailyTask import MultiAccountDailyTask
from src.task.WWOneTimeTask import WWOneTimeTask


class MultiAccountGardenTask(MultiAccountDailyTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🎡 Multi Account Garden 多账号周常乐园"
        self.description = ("Automatically switch accounts and run Weekly Garden for each account. "
                            "自动切换账号，为每个账号刷满周常乐园（已满 6000 分的自动跳过）。")

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()

        # 首个账号：当前已登录的账号，直接开刷
        self._run_garden()
        self.ensure_main(time_out=100)
        self._switch_to_login()
        self._mark_done(self._detect_current_account_from_login())
        self.info_set('Completed', self.done_set)

        # 之后逐个切换剩余账号，直到下拉列表里没有未完成的账号
        while True:
            try:
                next_account = self._select_and_login_account()
            except CannotFindException:
                # 所有账号均已完成（官方多账号任务此处直接抛出，这里接住并正常结束）
                self.log_info(self.tr('All accounts completed'))
                break
            if not next_account:
                break
            self.info_set('Completed', self.done_set)
            self._run_garden()
            self._mark_done(next_account)
            self.ensure_main(time_out=100)
            self._switch_to_login()

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
