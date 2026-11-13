# ============================================================
#  代理设置脚本 (PowerShell) - 点源方式加载
#  用法: . .\set_proxy.ps1
# ============================================================
$env:HTTP_PROXY  = "http://F2703535:KWTnX4u5@10.194.203.192:3128"
$env:HTTPS_PROXY = "http://F2703535:KWTnX4u5@10.194.203.192:3128"
$env:NO_PROXY    = "localhost,127.0.0.1,10.193.204.*"
Write-Host "代理已设置:"
Write-Host "  HTTP_PROXY  = $env:HTTP_PROXY"
Write-Host "  HTTPS_PROXY = $env:HTTPS_PROXY"
Write-Host "  NO_PROXY    = $env:NO_PROXY"
