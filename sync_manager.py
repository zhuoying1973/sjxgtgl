import sys
import paramiko
import os
import tarfile

hostname = "8.141.3.238"
port = 22
username = "root"
password = "ShengJing2023#"
remote_dir = "/var/www/archviz-biz-manager"
local_dir = os.path.dirname(os.path.abspath(__file__))

def pull_from_server():
    print("正在拉取云端最新代码...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname, port, username, password)
    
    print("1/3 在服务器上打包文件...")
    stdin, stdout, stderr = ssh.exec_command("cd /var/www && tar -czf archviz.tar.gz archviz-biz-manager")
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        print(f"打包失败: {stderr.read().decode('utf-8')}")
        return

    print("2/3 下载文件到本地...")
    sftp = ssh.open_sftp()
    remote_tar = "/var/www/archviz.tar.gz"
    local_tar = os.path.join(local_dir, "archviz.tar.gz")
    sftp.get(remote_tar, local_tar)
    sftp.close()
    
    ssh.exec_command("rm -f /var/www/archviz.tar.gz")
    ssh.close()
    
    print("3/3 在本地解压文件...")
    with tarfile.open(local_tar, 'r:gz') as tar_ref:
        tar_ref.extractall(os.path.dirname(local_dir))
    
    os.remove(local_tar)
    print("拉取完成！")

def push_to_server():
    print("正在保存最新代码到云端...")
    local_tar = os.path.join(local_dir, "archviz_local.tar.gz")
    folder_name = os.path.basename(local_dir)
    
    print("1/3 在本地打包文件...")
    with tarfile.open(local_tar, "w:gz") as tar:
        tar.add(local_dir, arcname=folder_name, filter=lambda x: None if any(ignore in x.name for ignore in ['.venv', '.git', '__pycache__', 'backups']) else x)
                
    print("2/3 上传文件到服务器...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname, port, username, password)
    sftp = ssh.open_sftp()
    
    remote_tar = "/var/www/archviz_local.tar.gz"
    sftp.put(local_tar, remote_tar)
    sftp.close()
    
    print("3/3 在服务器上解压文件...")
    stdin, stdout, stderr = ssh.exec_command("cd /var/www && tar -xzf archviz_local.tar.gz && rm archviz_local.tar.gz")
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        print(f"解压失败: {stderr.read().decode('utf-8')}")
    
    # Restart the service
    ssh.exec_command("systemctl restart archviz-biz-manager") # assuming a service exists, or handled via deploy script
    ssh.close()
    
    os.remove(local_tar)
    print("保存云端完成！")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "pull":
            pull_from_server()
        elif sys.argv[1] == "push":
            push_to_server()
