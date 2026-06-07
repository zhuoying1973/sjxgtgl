import os
import shutil
import re
import zipfile

SRC_DIR = r"c:\Users\Administrator\CascadeProjects\archviz-biz-manager"
DEPLOY_DIR = r"c:\Users\Administrator\CascadeProjects\ArchViz_Deploy"

def ignore_patterns(path, names):
    return [n for n in names if n in {'.git', '.venv', '__pycache__', '.vscode', 'backups', 'deployment_guide.md', 'ArchViz_Deploy', 'ArchViz_Deploy.zip'}]

def main():
    if os.path.exists(DEPLOY_DIR):
        shutil.rmtree(DEPLOY_DIR)
    
    print(f"Copying files from {SRC_DIR} to {DEPLOY_DIR}...")
    shutil.copytree(SRC_DIR, DEPLOY_DIR, ignore=ignore_patterns)

    print("Source code already patched for Python 3.8 compatibility in main.py.")

    # Create helper batch files
    with open(os.path.join(DEPLOY_DIR, "1_install_dependency.bat"), 'w', encoding='gbk') as f:
        f.write("@echo off\n")
        f.write("chcp 936 >nul\n")
        f.write("echo 正在安装依赖 (添加国内镜像源并增加超时时间)...\n")
        f.write("pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --default-timeout=100\n")
        f.write("echo.\n")
        f.write("echo 安装完成。\n")
        f.write("pause\n")

    with open(os.path.join(DEPLOY_DIR, "2_start_server.bat"), 'w', encoding='gbk') as f:
        f.write("@echo off\n")
        f.write("chcp 936 >nul\n")
        f.write("title ArchViz 业务管理系统 - 后台服务\n")
        f.write("set PYTHONPATH=.\n")
        f.write("echo 正在启动 ArchViz 后台服务...\n")
        f.write("echo 提示：如果要后台静默运行，请双击 4_silent_start.vbs\n")
        f.write("python -m backend.main\n")
        f.write("pause\n")

    with open(os.path.join(DEPLOY_DIR, "3_debug_check.bat"), 'w', encoding='gbk') as f:
        f.write("@echo off\n")
        f.write("chcp 936 >nul\n")
        f.write("echo 正在运行启动诊断程序...\n")
        f.write("set PYTHONPATH=.\n")
        f.write("python check_server_debug.py\n")
        f.write("pause\n")

    with open(os.path.join(DEPLOY_DIR, "4_silent_start.vbs"), 'w', encoding='gbk') as f:
        f.write('Set WshShell = CreateObject("WScript.Shell")\n')
        f.write('strPath = WshShell.CurrentDirectory\n')
        f.write('WshShell.Run "cmd /c set PYTHONPATH=. && python -m backend.main", 0, False\n')
        f.write('MsgBox "ArchViz 系统已在后台启动成功！" & vbCrLf & "现在可以通过浏览器访问了。", 64, "启动成功"\n')
        f.write('Set WshShell = Nothing\n')

    # Copy debug script
    shutil.copy(os.path.join(SRC_DIR, "check_server_debug.py"), os.path.join(DEPLOY_DIR, "check_server_debug.py"))

    with open(os.path.join(DEPLOY_DIR, "README_SERVER.txt"), 'w', encoding='gbk') as f:
        f.write("ArchViz 业务管理系统 - 维护说明\n")
        f.write("======================================\n\n")
        f.write("1. 重新启动系统：\n")
        f.write("   - 方式A (普通)：双击 2_start_server.bat (会显示黑色窗口)\n")
        f.write("   - 方式B (后台)：双击 4_silent_start.vbs (不显示窗口，后台运行)\n\n")
        f.write("2. 设置开机自启动：\n")
        f.write("   - 右键点击 4_silent_start.vbs -> 创建快捷方式。\n")
        f.write("   - 将该快捷方式 拖入 系统的“启动”文件夹即可。\n")
        f.write("   - (开始菜单 -> 所有程序 -> 启动)\n\n")
        f.write("3. 访问端口：http://localhost:8000\n")

    # Zip it
    zip_path = r"c:\Users\Administrator\CascadeProjects\ArchViz_Deploy_Win2008.zip"
    print(f"Zipping to {zip_path}...")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(DEPLOY_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, start=DEPLOY_DIR)
                zipf.write(file_path, arcname)
    
    print("Done.")

if __name__ == "__main__":
    main()
