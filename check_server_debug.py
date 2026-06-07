import sys
import traceback

print("=" * 50)
print("   ArchViz 系统启动调试辅助工具")
print("=" * 50)
print(f"当前 Python 版本: {sys.version}")
print("-" * 50)

try:
    print("正在尝试加载 backend.main 模块...")
    import backend.main
    print("模块加载成功！说明语法没有问题。")
    print("正在尝试初始化启动逻辑...")
    # 这里模拟 main 模块中的部分逻辑，但不真正运行死循环
    print("就绪。")
except Exception as e:
    print("\n[!!!] 捕获到启动错误 [!!!]")
    print("-" * 50)
    traceback.print_exc()
    print("-" * 50)
    print("\n请将上面的报错信息截图或复制发给 AI 助手分析。")

print("\n调试结束，按回车键退出...")
input()
