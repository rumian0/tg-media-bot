import os
import time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters
)
# 改用后台调度器，不占用async事件循环，部署不会崩
from apscheduler.schedulers.background import BackgroundScheduler

# 加载环境变量
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise Exception("请在.env配置 BOT_TOKEN=你的Bot Token")

# 全局调度器，时区上海
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# ===================== 定时任务示例（可替换成你自己的清理逻辑） =====================
def clean_expire_hash():
    """定时执行的清理任务，每5分钟跑一次"""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 执行过期数据清理")
    # 这里写你的业务：清理缓存、过期记录、临时文件等
    pass

# ===================== 指令回调函数 =====================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 机器人已正常运行\n"
        "/help 查看帮助\n"
        "/send 测试推送\n"
        "/auto 开关自动任务"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """📋 指令列表
/start 重启提示
/help 帮助
/send 测试消息推送
/auto 自动任务开关
发送图片/视频可触发媒体处理"""
    await update.message.reply_text(text)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ 测试消息发送成功")

async def auto_switch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏰ 自动定时任务运行中")

# 处理图片、视频
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已收到媒体文件，正在处理...")

# 处理普通文字消息
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    await update.message.reply_text(f"收到：{msg}")

# ===================== 主启动入口（官方标准，部署零报错） =====================
def main():
    # 构建TG应用，云服务器不要加proxy_url
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 绑定命令处理器
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("auto", auto_switch_cmd))

    # 媒体过滤器：照片+视频
    media_filter = filters.PHOTO | filters.VIDEO
    app.add_handler(MessageHandler(media_filter, media_handler))

    # 纯文本、排除指令
    text_filter = filters.TEXT & ~filters.COMMAND
    app.add_handler(MessageHandler(text_filter, text_handler))

    # 启动定时任务：每5分钟执行清理
    scheduler.add_job(clean_expire_hash, trigger="interval", minutes=5)
    scheduler.start()
    print("✅ 定时后台任务已启动")

    # TG机器人正式轮询启动（唯一一行启动代码，不嵌套asyncio.run）
    print("🤖 Telegram Bot 启动完成，开始接收消息")
    app.run_polling()

    # 机器人停止后优雅关闭调度器
    scheduler.shutdown()
    print("✅ 定时调度器已关闭")

if __name__ == "__main__":
    main()
