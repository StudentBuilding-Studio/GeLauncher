# 微软账号登录：一次性准备（约 5 分钟）

启动器走的是**设备代码流**（device code）：界面显示一串代码和一个网址，你在浏览器里登录一次就行。
这需要你先在 Azure 里注册一个"应用"，拿到 **client id** 填进启动器。免费，不用信用卡。

> 用哪种微软账号都行（Outlook / Hotmail / Live / Xbox 账号都算"个人 Microsoft 帐户"）。

## 1. 打开 Azure 门户

<https://portal.azure.com> → 用你的微软账号登录 → 搜索框输入 **Microsoft Entra ID**（旧名 Azure Active Directory）→ 进去。

## 2. 注册应用

1. 左侧选 **应用注册**（App registrations）→ 点 **新注册**（New registration）。
2. **名称**：随便填，比如 `GeLauncher`。
3. **受支持的帐户类型**：选 **仅个人 Microsoft 帐户**（Personal Microsoft accounts only）。
   - 这一步很关键：选成"组织目录"的账号类型，Xbox / Minecraft 会拒绝登录。
4. **重定向 URI**：留空（设备代码流不需要）。
5. 点 **注册**。

## 3. 复制 client id

注册完成后页面顶部有一行 **应用程序(客户端) ID**（Application (client) ID），形如
`12345678-1234-1234-1234-1234567890ab`。复制它。

## 4. 允许公共客户端流（必做）

左侧 **身份验证**（Authentication）→ 拉到底部 **高级设置**（Advanced settings）→
把 **允许公共客户端流**（Allow public client flows）切到 **是**（Yes）→ 点 **保存**。

不做这一步，设备代码流会直接被拒绝（返回 `unauthorized_client`）。

## 5. 填进启动器

两种方式任选：

```bash
# 命令行
python -m gelauncher config set msa-client-id 12345678-1234-1234-1234-1234567890ab
```

或者直接写进配置文件 `%APPDATA%\GeLauncher\config.json` 的 `"msa_client_id"` 字段。
之后在界面的「账号 → 添加账号 → 微软账号…」里登录即可。

## 常见报错对照

| 报错 | 原因 |
|---|---|
| `unauthorized_client` / `AADSTS700016` | client id 填错，或者第 4 步没开"允许公共客户端流" |
| `AADSTS50020` / 提示账号类型不支持 | 第 2 步的"受支持的帐户类型"选错了，要选"仅个人 Microsoft 帐户" |
| `2148916233`（XSTS） | 这个微软账号没有 Xbox 档案，先去 xbox.com 登录一次创建 |
| `2148916238`（XSTS） | 未成年账号，需要家长通过 Microsoft Family 批准 |
| 登录成功但提示"没有 Minecraft" | 这个账号没买 Java 版 Minecraft |

---

## 没有国际信用卡怎么办（2026 年的现实）

先说实话：**微软把"个人账号注册应用"这条路堵得很死**。

- 个人微软账号已经**不能在目录外注册应用**，门户提示
  "The ability to create applications outside of a directory has been deprecated"
- Azure 免费版 / Entra ID 免费版**要信用卡验证**（国内卡基本过不了这一关）
- Microsoft 365 开发者计划对独立开发者经常**静默拒绝**
  （"You don't currently qualify for a sandbox subscription"）

社区里有一整批独立开发者撞的是同一堵墙，可参考
[这个 Q&A](https://learn.microsoft.com/en-nz/answers/questions/6042112/no-path-for-a-solo-developer-to-get-an-entra-clien)
和 [专门问 Minecraft 启动器的那个](https://learn.microsoft.com/en-in/answers/questions/6049566/how-can-i-register-a-microsoft-oauth-application-f)。

### 还能试的（按可行性排）

1. **Power Apps 免费注册**（官方文档里唯一"可能零卡"的路）
   - 打开 <https://learn.microsoft.com/power-apps/maker/signup-for-powerapps#need-an-account>
   - 选 Start free，用你的微软账号登录
   - 如果出现 **Create a free Microsoft Entra account**，就建一个
   - 用新建的 `你@xxx.onmicrosoft.com` 登录 <https://entra.microsoft.com> → 应用注册
   - 这个租户在 Azure 里注册应用时，"受支持的帐户类型"要选
     **任何组织目录中的账户和个人 Microsoft 帐户**（多租户 + 个人）
2. **学生**：Azure for Students 免卡，用学校邮箱验证即可
   （<https://learn.microsoft.com/azure/education-hub/about-azure-for-students>）
3. **借朋友的 Azure 账号**注册一次：client id 不是秘密，注册完把 id 给你就能用
4. **用别的启动器的 client id**：技术上能跑，但那是别人的应用，随时可能失效，
   也不符合对方的使用条款，**本启动器不会内置这种配置**

### 关于登录端点（authority）

- 应用注册在**个人账号**上 → 用 `consumers`
- 应用注册在**组织租户**里（上面第 1/3 条路）→ 用 `common`

启动器默认 `consumers`，连不上时会**自动再试 `common`**，成功后记住用的是哪个，
配置里对应字段是 `msa_authority`。

### 还有一个可能的坑

就算拿到了 client id，微软的 `authentication/login_with_xbox` 也可能对"未获批的应用"
返回 403——这是微软对 Xbox Live / Minecraft 服务的策略限制，不是启动器的 bug。
真遇到了把错误发过来，我们再一起看有没有别的路。

