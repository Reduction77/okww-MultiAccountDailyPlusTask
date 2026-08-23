# -*- coding: utf-8 -*-
"""
自定义任务：多账号每日+（每个账号可自选刷第几个无音区）

本文件放在 ok_tasks/ 目录下即可被 ok-ww 自动加载，不修改任何官方源码，
上游更新（git pull / 覆盖安装）后无需重做。

用法：在任务配置的「Per-Account Tacet 每账号无音区」里每行添加一条：
    打码账号=无音区编号
例如：159****19oo=3
账号名以登录界面显示的打码账号为准（含 4 个星号）；
0/o、大小写、.con/.com 差异会自动容错。
未配置的账号仍使用「每日任务」里的全局设置；整个列表留空时与官方多账号任务行为一致。
"""

from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.DailyTask import DailyTask
from src.task.MultiAccountDailyTask import MultiAccountDailyTask, normalize_account_name
from src.task.TacetTask import TacetTask
from src.task.WWOneTimeTask import WWOneTimeTask

PER_ACCOUNT_TACET = 'Per-Account Tacet 每账号无音区'
TACET_INDEX_KEY = 'Which Tacet Suppression to Farm'  # 必须与 DailyTask 的配置键保持一致


class MultiAccountDailyPlusTask(MultiAccountDailyTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "👥 Multi Account Daily+ 多账号每日+"
        self.description = ("Automatically switch accounts and run Daily Task for each account, "
                            "with per-account Tacet Suppression selection. "
                            "多账号一条龙加强版：每个账号可自选刷第几个无音区。")
        self.default_config[PER_ACCOUNT_TACET] = []
        self.config_description[PER_ACCOUNT_TACET] = (
            '可选。每行一条：打码账号=无音区编号，例如 159****19oo=3。'
            '匹配到的账号会覆盖「每日任务」的「刷第几个无音区」设置；未配置的账号仍用每日任务的设置。 '
            'Optional: masked_account=tacet_number, one per line, e.g. 159****19oo=3.'
        )

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()
        overrides = self._parse_account_tacet_overrides()

        current_account = None
        if overrides:
            # 配置了覆盖时，先识别首个账号再刷，保证它的自选无音区也能生效
            self.ensure_main(time_out=100)
            self._switch_to_login()
            current_account = self._detect_current_account_from_login()
            self._click_login_button()

        self._run_daily_for_account(current_account, overrides)
        self.ensure_main(time_out=100)
        self._switch_to_login()
        detected = self._detect_current_account_from_login()
        self._mark_done(detected if detected else current_account)

        self.info_set('Completed', self.done_set)

        while next_account := self._select_and_login_account():
            self.info_set('Completed', self.done_set)
            self._run_daily_for_account(next_account, overrides)
            self._mark_done(next_account)
            self.ensure_main(time_out=100)
            self._switch_to_login()

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
            self.log_info('Per-account tacet overrides 每账号无音区: {}'.format(overrides))
        return overrides

    def _run_daily_for_account(self, account, overrides):
        index = overrides.get(normalize_account_name(account)) if account else None
        if index is None:
            self.run_task_by_class(DailyTask)
            return
        self.log_info('Account {} farms Tacet Suppression #{} 该账号刷第 {} 个无音区'.format(account, index, index))
        daily_task = self.get_task_by_class(DailyTask)
        old_value = daily_task.config.get(TACET_INDEX_KEY)
        # 只做内存级覆盖，绕过 Config.__setitem__，不写入每日任务的持久化配置
        dict.__setitem__(daily_task.config, TACET_INDEX_KEY, index)
        try:
            self.run_task_by_class(DailyTask)
        finally:
            dict.__setitem__(daily_task.config, TACET_INDEX_KEY, old_value)

    def _click_login_button(self):
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
