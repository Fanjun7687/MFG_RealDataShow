@echo off
rem ============================================================
rem  代理设置脚本 (CMD) - 请用 set_proxy.cmd 方式运行或直接 call
rem  用法: call set_proxy.cmd
rem ============================================================
set HTTP_PROXY=http://F2703535:KWTnX4u5@10.194.203.192:3128
set HTTPS_PROXY=http://F2703535:KWTnX4u5@10.194.203.192:3128
set NO_PROXY=localhost,127.0.0.1,10.193.204.*
echo 代理已设置:
echo   HTTP_PROXY  = %HTTP_PROXY%
echo   HTTPS_PROXY = %HTTPS_PROXY%
echo   NO_PROXY    = %NO_PROXY%
