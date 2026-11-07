# 注册邮箱验证码与 QQ 发信

卖家注册先输入收件邮箱，点击“获取验证码”，在自己的邮箱收取6位数字，再填写密码并注册。验证码10分钟有效；同一邮箱至少间隔60秒发送，重发成功后旧码失效；连续输错5次需重新获取。登录继续使用邮箱和密码，不要求每次收验证码。

密码规则：**8–12位**，英文字母、数字、常用英文特殊符号中至少包含两类，不能含空格。大小写字母算同一类。注册前后端都校验；已有账号登录仍按原密码验证。

## 发件配置（一次）

本机根目录 `.env` 已设置用户指定的 QQ 发件邮箱。授权码保存在 `SMTP_PASSWORD`，文件已被 Git 忽略。其他机器在 `.env` 填写：

```dotenv
MAIL_MODE=smtp
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的QQ邮箱@qq.com
SMTP_PASSWORD=QQ邮箱生成的SMTP授权码
SMTP_FROM_EMAIL=你的QQ邮箱@qq.com
SMTP_TIMEOUT_SECONDS=12
```

授权码并非 QQ 登录密码。按 [QQ 邮箱官方说明](https://help.mail.qq.com/detail/106/985)，网页登录邮箱后，进入右上角头像 → 设置 → 账号与安全 → 安全设置，开启 POP3/IMAP/SMTP 服务并生成授权码。修改 QQ 密码后，原授权码可能失效，需要重新生成。

本机修改 `.env` 后重启 `company-api`；修改数据库结构后同时重启 worker。首次更新此版本先运行：

```text
uv sync --project apps/server --locked --group dev
uv run --project apps/server company-db-init
```

本机已完成 `0010_registration_email` 迁移及重启；迁移不会删除已有账号或订单。之后访问 `/seller/register` 即可发码。`node scripts/check-env.mjs` 只检查授权码是否存在，真实发码成功才验证 SMTP 登录和提交邮件；SMTP 接受邮件不等于已确认收件箱到达，未看到请检查垃圾邮件和邮箱是否填写正确。

SMTP 使用 SSL/TLS 并通过 certifi 校验证书，兼容本机 Python 默认信任链缺失的情况。生产环境没有验证码旁路，未配置 SMTP 或发送失败时直接提示失败，验证码不会出现在接口响应或日志里，数据库只保存带密钥摘要。

## 自动测试与模型烟测

`node scripts/test-stack.mjs` 使用独立测试数据库及临时邮件目录，模拟邮箱收信后完成注册和订单全流程；不会给真实邮箱发测试邮件。测试邮件模式仅允许 `APP_ENV=test`，生产和普通开发均拒绝启用。

需要手动运行真实 MiMo 的 `smoke-intake.py` / `smoke-closed-loop.py` 时，先在网页注册一个完成邮箱验证的测试卖家，在被忽略的 `var/local-test-seller.txt` 保存两行 `email=...`、`password=...`。公网烟测使用公网库中已验证账号，对应 `var/public-test-seller.txt`。这些脚本登录已有测试账号，不绕过邮箱验证；文件不要分享或加入 Git。

本机与临时公网演示是独立数据库，需分别注册；公网脚本沿用本机私有 SMTP 配置。当前未实现忘记密码功能。
