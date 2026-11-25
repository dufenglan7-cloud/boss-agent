# 固定监听本机地址的简易启动命令；开发热重载可直接使用 uvicorn。
# 使用既有 Python 环境启动本地 Uvicorn 服务，不在这里安装依赖。
def main():
    import uvicorn
    uvicorn.run("boss_agent.main:app", host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()
