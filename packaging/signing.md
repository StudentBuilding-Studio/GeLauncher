# 代码签名与 SmartScreen 说明

## 为什么会弹「Windows 已保护你的电脑」

这是 **Microsoft Defender SmartScreen**，不是杀毒报毒。判断依据是：

- exe 没有受信任的**代码签名证书**
- 或者有签名但**下载量/信誉不够**（新文件、新发布者）

它拦的是"没见过的程序"，不是检测到病毒。用户可以点「更多信息 → 仍要运行」，或右键 exe → 属性 → 勾选「解除锁定」。

## 签名能不能解决

**能，但必须买受信任的代码签名证书。** 自己用 `New-SelfSignedCertificate` 生成的证书：

- 本机可以装进"受信任的根证书颁发机构"来消除警告
- **别人电脑上依然会弹 SmartScreen**（因为没装你的自签名根证书）
- 所以自签名只适合自己测试，不能用于分发

## 可选的证书类型

| 方案 | 价格量级 | SmartScreen 效果 | 备注 |
|---|---|---|---|
| **EV 代码签名证书** | 约 $300–500/年 | 拿到证书后信誉立刻建立，通常不弹 | 需要 USB 硬件令牌 / HSM，不能导出 pfx |
| **OV 代码签名证书** | 约 $100–250/年 | 签名有效，但新发布者仍可能弹，随下载量积累信誉 | 需要企业/个人身份审核，可导出 pfx |
| **Azure Trusted Signing** | 约 $10/月 | 微软云签名，配合信誉逐步建立 | 需要 Azure 订阅，组织需 3 年历史；个人账户有额外要求 |
| 自签名证书 | 免费 | 无效（别人电脑照样弹） | 仅本机调试 |

> 结论：想彻底不弹，最省事的是 OV/EV 证书；只是朋友之间用，让大家点「仍要运行」或改用 zip 分发 + 说明也可以。

## 证书拿到后怎么签

仓库里的 `packaging/build.ps1` 已经预留了签名钩子，设置两个环境变量即可：

```powershell
$env:GE_SIGN_PFX = "C:\certs\gelauncher.pfx"
$env:GE_SIGN_PASSWORD = "你的密码"
powershell -ExecutionPolicy Bypass -File packaging/build.ps1
```

脚本会调用 Windows SDK 里的 `signtool`：

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /f cert.pfx /p password dist\GeLauncher\GeLauncher.exe
```

没有 pfx（EV 证书在硬件令牌里）时，用证书存储里的证书：

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\GeLauncher\GeLauncher.exe
```

签名后可以用以下命令确认：

```powershell
Get-AuthenticodeSignature dist\GeLauncher\GeLauncher.exe
```

`Status` 应该是 `Valid`。

## 已经做的其它降低误报的措施

- exe 带完整的**版本资源**（公司/产品/文件版本），不是裸 PyInstaller 包
- 带 **manifest**：`asInvoker`（不要求管理员）、PerMonitorV2 DPI、声明支持 Win10/11
- 目录分发（非单文件），不压缩（`upx=False`），减少启发式误报
- 有图标和正常的窗口/文件名

如果仍然被 Defender 报毒（不是 SmartScreen），可以到
[Microsoft 安全情报提交](https://www.microsoft.com/en-us/wdsi/filesubmission) 上传 exe 申诉误报。
