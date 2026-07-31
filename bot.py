import os
import time
import asyncio
from dotenv import load_dotenv
from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters
)
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise Exception("请配置.env中的BOT_TOKEN")

# 定时任务调度器
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# ================= 消息聚合全局变量 =================
# key: 用户ID, value: 媒体列表 + 延时任务
user_media_queue = dict()
# 等待多久自动打包发送（单位：秒）
AGGREGATE_WAIT_SEC = 3

# 定时清理过期缓存（保留原有）
def clean_expire_hash():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 清理用户媒体缓存")

# ================= 打包发送媒体组核心函数 =================
async def send_media_group(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """将缓存的多张图/视频聚合为一条合并消息发送"""
    if user_id not in user_media_queue or len(user_media_queue[user_id]["media"]) == 0:
        return
    media_list = []
    for item in user_media_queue[user_id]["media"]:
        if item["type"] == "photo":
            media_list.append(InputMediaPhoto(media=item["file_id"]))
        elif item["type"] == "video":
            media_list.append(InputMediaVideo(media=item["file_id"]))

    # 批量聚合发送
    try:
        await context.bot.send_media_group(chat_id=chat_id, media=media_list)
        await context.bot.send_message(chat_id=chat_id, text="✅ 所有媒体已聚合打包发送完成")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ 聚合发送失败：{str(e)}")

    # 清空当前用户队列
    del user_media_queue[user_id]

# ================= 媒体接收处理器（重构聚合逻辑） =================
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    uid = user.id
    msg = update.effective_message

    # 初始化用户队列
    if uid not in user_media_queue:
        user_media_queue[uid] = {
            "media": [],
            "task": None
        }

    # 判断是图片还是视频，提取file_id存入队列
    if msg.photo:
        file_id = msg.photo[-1].file_id
        user_media_queue[uid]["media"].append({"type": "photo", "file_id": file_id})
    elif msg.video:
        file_id = msg.video.file_id
        user_media_queue[uid]["media"].append({"type": "video", "file_id": file_id})

    # 如果上一次的延时任务还在运行，取消掉，重新倒计时
    task_info = user_media_queue[uid]["task"]
    if task_info and not task_info.done():
        task_info.cancel()

    # 新建延时任务：等待N秒后自动打包发送
    async def delay_send():
        await asyncio.sleep(AGGREGATE_WAIT_SEC)
        await send_media_group(uid, chat.id, context)

    new_task = asyncio.create_task(delay_send())
    user_media_queue[uid]["task"] = new_task

    # 只回复一次入队提示，不再每条都刷屏
    if len(user_media_queue[uid]["media"]) == 1:
        await update.message.reply_text("📦 已开始收集媒体，继续发送图片/视频，停顿3秒自动合并发送")

# ================= 命令与文本处理器 =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 媒体聚合机器人\n直接连续发多张图片/视频，会自动打包成一条合并消息")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("使用方法：连续发送多张照片或视频，停止发送3秒后自动聚合为合集发送")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text(f"收到文字：{text}")

# ================= 主启动入口 =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 注册命令
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    # 媒体处理器：图片+视频
    media_filter = filters.PHOTO | filters.VIDEO
    app.add_handler(MessageHandler(media_filter, media_handler))

    # 纯文本
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # 后台定时清理
    scheduler.add_job(clean_expire_hash, trigger="interval", minutes=5)
    scheduler.start()
    print("✅ 定时缓存清理任务启动")

    print("🤖 TG媒体聚合Bot启动完成，支持多图自动打包")
    app.run_polling()

    scheduler.shutdown()
    print("✅ 调度器已关闭")

if __name__ == "__main__":
    main()
