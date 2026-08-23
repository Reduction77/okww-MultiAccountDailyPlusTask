# okww-MultiAccountDailyPlusTask

适用于 [ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)（ok-ww）的自定义任务：**多账号每日+**。

在官方「多账号每日任务」的基础上，支持**每个账号自选刷第几个无音区**。

## 环境要求

- ok-ww 版本不早于 2026-05（约 v3.5+，即官方「多账号每日任务」重构之后的版本，最新版直接用即可）
- 无需修改任何官方源码，上游更新（覆盖安装 / 在线更新）后无需重装本任务

## 安装

把 `MultiAccountDailyPlusTask.py` 放进 ok-ww 安装目录下的 `ok_tasks/` 文件夹，重启 ok-ww 后任务列表里会出现 **「👥 Multi Account Daily+ 多账号每日+」**。

## 使用方法

1. 在「每日任务」里确认 **Which to Farm 设为 Tacet Suppression（无音区）**——编号覆盖只有在这种情况下才生效；设成凝素领域/模拟领域时覆盖会被忽略。
2. 在本任务的配置项 **「Per-Account Tacet 每账号无音区」** 里，每行添加一条：

   ```
   打码账号=无音区编号
   ```

   例如：

   ```
   159****19oo=3
   abc****def@gmail.com=7
   ```

3. 账号名以登录界面显示的打码账号为准（含 4 个星号）；`0/o`、大小写、`.con/.com` 差异会自动容错。
4. 未配置的账号仍使用「每日任务」里的全局设置；整个列表留空时行为与官方多账号任务完全一致。

无音区编号从 1 开始，对应 F2 指南书里的顺序，上限自动跟随 ok-ww 的 TacetTask（当前共 19 个）。

## 实现说明

- 只做内存级覆盖（`dict.__setitem__`），不会改动「每日任务」的持久化配置
- 编号上限动态读取 `TacetTask.total_number`，游戏新增无音区后无需改脚本
- 若 ok-ww 更新后每日任务的配置键改名，脚本会输出警告并回退到全局设置，不会静默刷错本
