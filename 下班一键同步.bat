@echo off
chcp 65001 > nul
echo ===================================================
echo             效果图公司管理系统 - 一键下班同步工具
echo ===================================================
echo.

echo [1/4] 正在更新本地 PDF 对话和思考记录归档...
if exist "E:\My_AI_Projects\export_pdfs3.py" (
    python "E:\My_AI_Projects\export_pdfs3.py"
) else (
    echo [警告] 未找到 PDF 导出脚本 E:\My_AI_Projects\export_pdfs3.py，跳过 PDF 归档。
)
echo.

echo [2/4] 正在添加代码改动到 Git 暂存区...
git add .
echo.

echo [3/4] 正在提交代码 (自动记录时间)...
set datetime=%date% %time%
git commit -m "自动同步: %datetime%"
echo.

echo [4/4] 正在推送到远程 GitHub 仓库...
git push
echo.

echo [可选] 正在将代码同步部署到阿里云服务器...
python sync_manager.py push
echo.

echo ===================================================
echo [成功] 一键下班同步完成！
echo 1. 代码已保存并推送到 GitHub 远程仓库。
echo 2. 阿里云服务器已同步部署最新版本运行。
echo 3. 历史对话 PDF 归档已更新。
echo ===================================================
echo.
pause
