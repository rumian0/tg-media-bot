import os
import hashlib
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InputMediaPhoto, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
)

# 加载环境变量
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ====================== 全局配置 ======================
# 滑动时间窗口 30秒
SLIDE_WINDOW_SEC = 30
# 超时自动发送 60秒无新消息
AUTO_SEND_TIMEOUT = 60
# 单组合并最大数量
MAX_MEDIA_PER_GROUP = 5
# 重复文件校验有效期 5分钟
DUPLICATE_VALID_MIN = 5

# 调度器：处理超时自动发送
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# 用户缓存池
user_cache = dict()
# 重复文件哈希过期记录：{user_id: {md5: expire_time}}
dup_hash_record = dict()

# ====================== 工具函数 ======================
def get_file_md5(file_bytes: bytes) -> str:
    """计算文件MD5哈希用于去重"""
    md5 = hashlib.md5()
    md5.update(file_bytes)
    return md5.hexdigest()

def init_user_cache(user_id: int):
    """初始化用户缓存"""
    if user_id not in user_cache:
        user_cache[user_id] = {
            "media_list": [],
            "file_hash_set": set(),
            "last_receive_time": datetime.now(),
            "timeout_job_id": None,
            "enable_auto_merge": True,
            "max_count": MAX_MEDIA_PER_GROUP,
            "window_seconds": SLIDE_WINDOW_SEC
        }

def clean_expire_hash():
    """定时清理过期重复哈希记录（5分钟）"""
    now = datetime.now()
    del_users = []
    for uid, hash_map in dup_hash_record.items():
        del_md5 = []
        for h, exp in hash_map.items():
            if now > exp:
                del_md5.append(h)
        for h in del_md5:
            del hash_map[h]
        if not hash_map:
            del_users.append(uid)
    for uid in del_users:
        del dup_hash_record[uid]

async def flush_media_group(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """核心：推送合并媒体组并清空缓存"""
    if user_id not in user_cache:
        return
    cache = user_cache[user_id]
    media_list = cache["media_list"]
    if not media_list:
        return

    # 移除超时任务
    if cache["timeout_job_id"]:
        try:
            scheduler.remove_job(cache["timeout_job_id"])
        except Exception:
            pass

    # Telegram限制最多10个一组，拆分发送
    def chunk_list(lst, size=10):
        return [lst[i:i+size] for i in range(0, len(lst), size)]

    chunks = chunk_list(media_list, 10)
    for chunk in chunks:
        try:
            await context.bot.send_media_group(chat_id=chat_id, media=chunk)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"合并发送失败：{str(e)}")

    # 清空当前用户缓冲区
    cache["media_list"] = []
    cache["file_hash_set"] = set()
    cache["timeout_job_id"] = None

# ====================== 定时触发回调 ======================
async def auto_timeout_send(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """60秒超时自动发送"""
    await flush_media_group(user_id, chat_id, context)
    await context.bot.send_message(chat_id, text=f"超时{AUTO_SEND_TIMEOUT}秒未更新，已自动合并发送")

# ====================== 指令处理器 ======================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """📸 媒体自动合并相册机器人
功能说明：
1. 连续发图片/视频自动缓存，30秒滑动窗口聚合为一组发送
2. 攒够5张、60秒超时、发送【发送】文字均可手动触发合并
3. 5分钟内重复文件自动过滤不重复存入

指令：
/send  强制提交当前缓存
/auto  开关自动合并功能
/help  帮助"""
    await update.message.reply_text(text)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/send 手动强制发送"""
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await flush_media_group(uid, cid, context)
    await update.message.reply_text("✅ 已手动提交缓存媒体")

async def auto_switch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开启/关闭自动合并"""
    uid = update.effective_user.id
    init_user_cache(uid)
    cache = user_cache[uid]
    cache["enable_auto_merge"] = not cache["enable_auto_merge"]
    status = "已开启" if cache["enable_auto_merge"] else "已关闭"
    await update.message.reply_text(f"自动合并功能：{status}")

# ====================== 媒体文件核心处理 ======================
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = msg.from_user
    chat = msg.chat
    uid = user.id
    cid = chat.id
    init_user_cache(uid)
    cache = user_cache[uid]

    # 关闭自动合并则直接单发
    if not cache["enable_auto_merge"]:
        return

    # 1. 下载文件并计算MD5去重
    file_obj = None
    media_type = None
    try:
        if msg.photo:
            # 取最高分辨率图片
            file_id = msg.photo[-1].file_id
            media_type = "photo"
        elif msg.video:
            file_id = msg.video.file_id
            media_type = "video"
        else:
            # 文档/音频不加入媒体组，直接放行
            return

        # 下载文件字节流计算哈希
        file = await context.bot.get_file(file_id)
        file_bytes = await file.download_as_bytearray()
        file_md5 = get_file_md5(bytes(file_bytes))

        # 2. 校验5分钟内是否重复文件
        expire_time = datetime.now() + timedelta(minutes=DUPLICATE_VALID_MIN)
        if uid not in dup_hash_record:
            dup_hash_record[uid] = {}
        hash_map = dup_hash_record[uid]
        if file_md5 in hash_map:
            await msg.reply_text("⚠️ 检测到5分钟内重复文件，已跳过")
            return
        hash_map[file_md5] = expire_time

        # 3. 构造Telegram媒体对象
        caption = msg.caption if msg.caption else None
        if media_type == "photo":
            media_item = InputMediaPhoto(media=file_id, caption=caption)
        else:
            media_item = InputMediaVideo(media=file_id, caption=caption)

        # 4. 加入缓冲区
        cache["media_list"].append(media_item)
        cache["last_receive_time"] = datetime.now()

        # 5. 重置超时倒计时：删除旧任务，新建60秒自动发送任务
        if cache["timeout_job_id"]:
            try:
                scheduler.remove_job(cache["timeout_job_id"])
            except Exception:
                pass
        job = scheduler.add_job(
            auto_timeout_send,
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=AUTO_SEND_TIMEOUT),
            args=[uid, cid, context]
        )
        cache["timeout_job_id"] = job.id

        # 6. 达到最大数量直接触发发送
        if len(cache["media_list"]) >= cache["max_count"]:
            await flush_media_group(uid, cid, context)
            await msg.reply_text(f"✅ 已凑满{cache['max_count']}个文件，自动合并发送")

    except Exception as e:
        await msg.reply_text(f"媒体处理异常：{str(e)}")

# 纯文本消息处理：触发发送 + 指令「发送」
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    cid = update.effective_chat.id

    # 用户输入“发送”手动提交
    if text == "发送":
        await flush_media_group(uid, cid, context)
        await update.message.reply_text("✅ 手动提交完成")
        return

    # 普通文字消息：先发送缓存媒体，再展示文字
    await flush_media_group(uid, cid, context)

# ====================== 启动入口 ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 注册所有处理器不变
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("auto", auto_switch_cmd))
    app.add_handler(CommandHandler("help", start_cmd))

    media_filter = filters.PHOTO | filters.VIDEO
    app.add_handler(MessageHandler(media_filter, media_handler))
    text_filter = filters.TEXT & ~filters.COMMAND
    app.add_handler(MessageHandler(text_filter, text_handler))

    # 修复点1：把调度器挂载到bot的context，跟随async事件循环启动
    async def on_startup(context: ContextTypes.DEFAULT_TYPE):
        scheduler.add_job(clean_expire_hash, trigger="interval", minutes=5)
        scheduler.start()
        print("✅ 定时任务调度器已启动")

    async def on_shutdown(context: ContextTypes.DEFAULT_TYPE):
        scheduler.shutdown()
        print("✅ 调度器正常关闭")

    # 绑定生命周期钩子，在事件循环就绪后再启动scheduler
    app.add_startup_handler(on_startup)
    app.add_shutdown_handler(on_shutdown)

    print("Bot 启动成功，轮询中...")
    app.run_polling()

if __name__ == "__main__":
    main()
