import paramiko

hostname = "8.141.3.238"
port = 22
username = "root"
password = "ShengJing2023#"

def check_status():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname, port, username, password)
    
    print("=== 1. 检查 Systemd 服务状态 ===")
    stdin, stdout, stderr = ssh.exec_command("systemctl status archviz-manager.service")
    print(stdout.read().decode('utf-8'))
    print(stderr.read().decode('utf-8'))
    
    print("=== 2. 检查最近 30 行服务运行日志 ===")
    stdin, stdout, stderr = ssh.exec_command("journalctl -u archviz-manager.service -n 30 --no-pager")
    print(stdout.read().decode('utf-8'))
    print(stderr.read().decode('utf-8'))
    
    print("=== 3. 检查是否有冲突的 Uvicorn 进程 ===")
    stdin, stdout, stderr = ssh.exec_command("ps aux | grep uvicorn")
    print(stdout.read().decode('utf-8'))
    
    ssh.close()

if __name__ == "__main__":
    check_status()
