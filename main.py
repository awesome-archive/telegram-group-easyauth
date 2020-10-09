#!/usr/bin/env python
# -*- coding: utf-8 -*-
import copy
import datetime
import os
import re
import sys
import time
from io import BytesIO
from random import SystemRandom

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
    Poll,
)
from telegram.error import BadRequest
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
    PicklePersistence,
    Updater,
)
from telegram.ext.filters import MergedFilter
from telegram.utils.helpers import mention_markdown

from utils import (
    FullChatPermissions,
    collect_error,
    get_chat_admins,
    load_config,
    load_yml,
    load_yml_path,
    logger,
    save_yml,
)


def escape_markdown(text):
    # Use {} and reverse markdown carefully.
    parse = re.sub(r"([_*\[\]()~`>\#\+\-=|\.!])", r"\\\1", text)
    reparse = re.sub(r"\\\\([_*\[\]()~`>\#\+\-=|\.!])", r"\1", parse)
    return reparse


def start_command(update, context):
    message = update.message
    chat = message.chat
    user = message.from_user
    message.reply_text(
        escape_markdown(context.bot_data.get("config").get("START")).format(
            chat=chat.id, user=user.id
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    logger.info(
        f"Current Jobs: {[t.id for t in context.job_queue.scheduler.get_jobs()]}"
    )


def kick(context, chat_id, user_id):
    if context.bot.kick_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        until_date=int(time.time()) + context.bot_data.get("config").get("BANTIME"),
    ):
        logger.info(f"Job kick: Successfully kicked user {user_id} at group {chat_id}")
        return True
    else:
        logger.warning(
            f"Job kick: No enough permissions to kick user {user_id} at group {chat_id}"
        )
        return False


def restore(context, chat_id, user_id):
    if context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=FullChatPermissions,
    ):
        logger.info(
            f"Job restore: Successfully restored user {user_id} at group {chat_id}"
        )
        return True
    else:
        logger.warning(
            f"Job restore: No enough permissions to restore user {user_id} at group {chat_id}"
        )
        return False


def clean(context, chat_id, user_id, message_id):
    if context.bot.delete_message(chat_id=chat_id, message_id=message_id):
        logger.info(
            f"Job clean: Successfully delete message {message_id} from {user_id} at group {chat_id}"
        )
        return True
    else:
        logger.warning(
            f"Job clean: No enough permissions to delete message {message_id} from {user_id} at group {chat_id}"
        )
        return False


def newmem(update, context):
    message = update.message
    chat = message.chat
    if message.from_user.id in get_chat_admins(
        context.bot,
        chat.id,
        extra_user=context.bot_data.get("config").get("SUPER_ADMIN"),
    ):
        return
    for user in message.new_chat_members:
        if user.is_bot:
            continue
        num = SystemRandom().randint(
            0, len(context.bot_data.get("config").get("CHALLENGE")) - 1
        )
        flag = context.bot_data.get("config").get("CHALLENGE")[num]
        if context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
        ):
            logger.info(
                f"New member: Successfully restricted user {user.id} at group {chat.id}"
            )
        else:
            logger.warning(
                f"New member: No enough permissions to restrict user {user.id} at group {chat.id}"
            )
        buttons = [
            [
                InlineKeyboardButton(
                    flag.get("WRONG")[t],
                    callback_data=f"challenge|{user.id}|{num}|{flag.get('wrong')[t]}",
                )
            ]
            for t in range(len(flag.get("WRONG")))
        ]
        buttons.append(
            [
                InlineKeyboardButton(
                    flag.get("ANSWER"),
                    callback_data=f"challenge|{user.id}|{num}|{flag.get('answer')}",
                )
            ]
        )
        SystemRandom().shuffle(buttons)
        buttons.append(
            [
                InlineKeyboardButton(
                    context.bot_data.get("config").get("PASS_BTN"),
                    callback_data=f"admin|pass|{user.id}",
                ),
                InlineKeyboardButton(
                    context.bot_data.get("config").get("KICK_BTN"),
                    callback_data=f"admin|kick|{user.id}",
                ),
            ]
        )
        question_message = message.reply_text(
            escape_markdown(context.bot_data.get("config").get("GREET")).format(
                question=escape_markdown(flag.get("QUESTION")),
                time=context.bot_data.get("config").get("TIME"),
            ),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        context.job_queue.scheduler.add_job(
            kick,
            "date",
            id=f"{chat.id}|{user.id}|kick",
            name=f"{chat.id}|{user.id}|kick",
            args=[context, chat.id, user.id],
            run_date=datetime.datetime.now()
            + datetime.timedelta(seconds=context.bot_data.get("config").get("TIME")),
            replace_existing=True,
        )
        context.job_queue.scheduler.add_job(
            clean,
            "date",
            id=f"{chat.id}|{user.id}|clean_join",
            name=f"{chat.id}|{user.id}|clean_join",
            args=[context, chat.id, user.id, message.message_id],
            run_date=datetime.datetime.now()
            + datetime.timedelta(seconds=context.bot_data.get("config").get("TIME")),
            replace_existing=True,
        )
        context.job_queue.scheduler.add_job(
            clean,
            "date",
            id=f"{chat.id}|{user.id}|clean_question",
            name=f"{chat.id}|{user.id}|clean_question",
            args=[context, chat.id, user.id, question_message.message_id],
            run_date=datetime.datetime.now()
            + datetime.timedelta(seconds=context.bot_data.get("config").get("TIME")),
            replace_existing=True,
        )


def quiz_command(update, context):
    num = SystemRandom().randint(
        0, len(context.bot_data.get("config").get("CHALLENGE")) - 1
    )
    flag = context.bot_data.get("config").get("CHALLENGE")[num]
    answer = [flag.get("WRONG")[t] for t in range(len(flag.get("WRONG")))]
    SystemRandom().shuffle(answer)
    index = SystemRandom().randint(0, len(answer) - 1)
    answer.insert(index, flag.get("ANSWER"))
    update.effective_message.reply_poll(
        flag.get("QUESTION"),
        answer,
        correct_option_id=index,
        is_anonymous=False,
        open_period=context.bot_data.get("config").get("QUIZTIME"),
        type=Poll.QUIZ,
    )


def query(update, context):
    def query_callback(context, data):
        data = data.split("|")
        logger.info(f"Parse Callback: {data}")
        user_id = int(data[1])
        number = int(data[2])
        answer = str()
        answer_encode = data[3]
        question = (
            context.bot_data.get("config").get("CHALLENGE")[number].get("QUESTION")
        )
        if answer_encode == context.bot_data.get("config").get("CHALLENGE")[number].get(
            "answer"
        ):
            result = True
            answer = (
                context.bot_data.get("config").get("CHALLENGE")[number].get("ANSWER")
            )
        else:
            result = False
            for t in range(
                len(
                    context.bot_data.get("config").get("CHALLENGE")[number].get("wrong")
                )
            ):
                if (
                    answer_encode
                    == context.bot_data.get("config")
                    .get("CHALLENGE")[number]
                    .get("wrong")[t]
                ):
                    answer = (
                        context.bot_data.get("config")
                        .get("CHALLENGE")[number]
                        .get("WRONG")[t]
                    )
                    break
        logger.info(
            f"New challenge parse callback:\nuser_id: {user_id}\nresult: {result}\nquestion: {question}\nanswer: {answer}"
        )
        return user_id, result, question, answer

    callback_query = update.callback_query
    user = callback_query.from_user
    message = callback_query.message
    chat = message.chat
    user_id, result, question, answer = query_callback(context, callback_query.data)
    if user.id != user_id:
        callback_query.answer(
            text=context.bot_data.get("config").get("OTHER"),
            show_alert=True,
        )
        return
    cqconf = (
        context.bot_data.get("config").get("SUCCESS")
        if result
        else context.bot_data.get("config")
        .get("RETRY")
        .format(time=context.bot_data.get("config").get("BANTIME"))
    )
    callback_query.answer(
        text=cqconf,
        show_alert=False if result else True,
    )
    if result:
        conf = context.bot_data.get("config").get("PASS")
        restore(context, chat.id, user_id)
        if schedule := context.job_queue.scheduler.get_job(
            f"{chat.id}|{user.id}|clean_join"
        ):
            schedule.remove()
    else:
        if kick(context, chat.id, user_id):
            conf = context.bot_data.get("config").get("KICK")
        else:
            conf = context.bot_data.get("config").get("NOT_KICK")
    message.edit_text(
        escape_markdown(conf).format(
            user=user.mention_markdown_v2(),
            question=escape_markdown(question),
            ans=escape_markdown(answer),
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    if schedule := context.job_queue.scheduler.get_job(f"{chat.id}|{user.id}|kick"):
        schedule.remove()


def admin(update, context):
    def admin_callback(context, data):
        data = data.split("|")
        logger.info(f"Parse Callback: {data}")
        if data[1] == "pass":
            result = True
        else:
            result = False
        user_id = int(data[2])
        logger.info(f"New admin parse callback:\nuser_id: {user_id}\nresult: {result}")
        return result, user_id

    callback_query = update.callback_query
    user = callback_query.from_user
    message = callback_query.message
    chat = message.chat
    if user.id not in get_chat_admins(
        context.bot,
        chat.id,
        extra_user=context.bot_data.get("config").get("SUPER_ADMIN"),
    ):
        callback_query.answer(
            text=context.bot_data.get("config").get("OTHER"),
            show_alert=True,
        )
        return
    result, user_id = admin_callback(context, callback_query.data)
    cqconf = (
        context.bot_data.get("config").get("PASS_BTN")
        if result
        else context.bot_data.get("config").get("KICK_BTN")
    )
    conf = (
        context.bot_data.get("config").get("ADMIN_PASS")
        if result
        else context.bot_data.get("config").get("ADMIN_KICK")
    )
    callback_query.answer(
        text=cqconf,
        show_alert=False,
    )
    if result:
        restore(context, chat.id, user_id)
        if schedule := context.job_queue.scheduler.get_job(
            f"{chat.id}|{user_id}|clean_join"
        ):
            schedule.remove()
    else:
        kick(context, chat.id, user_id)
    message.edit_text(
        escape_markdown(conf).format(
            admin=user.mention_markdown_v2(),
            user=mention_markdown(user_id, str(user_id), version=2),
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    if schedule := context.job_queue.scheduler.get_job(f"{chat.id}|{user_id}|kick"):
        schedule.remove()


def admin_command(update, context):
    message = update.message
    message.reply_text(get_chat_admins(context.bot, message.chat.id, username=True))


def private_callback(data):
    data = data.split("|")
    logger.info(f"Parse Callback: {data}")
    if data[0] in [
        "detail_question_private",
        "edit_question_private",
        "delete_question_private",
    ]:
        number = int(data[1])
        logger.info(f"New private parse callback:\nresult: {number}")
        return number
    return


def reload_private(update, context):
    message = update.message
    logger.info(f"Private: Reloaded config")
    message.reply_text(reload_config(context))


def start_private(update, context):
    message = update.message
    callback_query = update.callback_query
    if callback_query:
        callback_query.answer()
        user = callback_query.from_user
    else:
        user = message.from_user
    if user.id not in get_chat_admins(
        context.bot,
        context.bot_data.get("config").get("CHAT"),
        extra_user=context.bot_data.get("config").get("SUPER_ADMIN"),
    ):
        logger.info(f"Private: User {user.id} is unauthorized, blocking")
        message.reply_text(
            context.bot_data.get("config").get("START_UNAUTHORIZED_PRIVATE")
        )
        return ConversationHandler.END
    keyboard = [
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("SAVE_QUESTION_BTN"),
                callback_data="save",
            )
        ],
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("ADD_NEW_QUESTION_BTN"),
                callback_data=f'edit_question_private|{len(context.bot_data.get("config").get("CHALLENGE"))}',
            )
        ],
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("LIST_ALL_QUESTION_BTN"),
                callback_data="list_question_private",
            )
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if callback_query:
        callback_query.edit_message_text(
            context.bot_data.get("config")
            .get("START_PRIVATE")
            .format(link=context.bot_data.get("config").get("CHAT")),
            reply_markup=markup,
        )
    else:
        message.reply_text(
            context.bot_data.get("config")
            .get("START_PRIVATE")
            .format(link=context.bot_data.get("config").get("CHAT")),
            reply_markup=markup,
        )
    logger.info("Private: Start")
    logger.info(
        f"Current Jobs: {[t.id for t in context.job_queue.scheduler.get_jobs()]}"
    )
    logger.debug(callback_query)
    return CHOOSING


@collect_error
def list_question_private(update, context):
    callback_query = update.callback_query
    callback_query.answer()
    logger.debug(context.bot_data.get("config").get("CHALLENGE"))
    keyboard = [
        [
            InlineKeyboardButton(
                flag.get("QUESTION"), callback_data=f"detail_question_private|{num}"
            )
        ]
        for (num, flag) in enumerate(context.bot_data.get("config").get("CHALLENGE"))
    ]
    keyboard.insert(
        0,
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("BACK"), callback_data="back"
            )
        ],
    )
    markup = InlineKeyboardMarkup(keyboard)
    callback_query.edit_message_text(
        context.bot_data.get("config").get("LIST_PRIVATE"), reply_markup=markup
    )
    logger.info("Private: List question")
    logger.debug(callback_query)
    return LIST_VIEW


@collect_error
def detail_question_private(update, context):
    callback_query = update.callback_query
    callback_query.answer()
    num = private_callback(callback_query.data)
    keyboard = [
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("BACK"), callback_data="back"
            )
        ],
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("EDIT_QUESTION_BTN"),
                callback_data=f"edit_question_private|{num}",
            )
        ],
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("DELETE_QUESTION_BTN"),
                callback_data=f"delete_question_private|{num}",
            )
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    flag = context.bot_data.get("config").get("CHALLENGE")[num]
    callback_query.edit_message_text(
        context.bot_data.get("config")
        .get("DETAIL_QUESTION_PRIVATE")
        .format(
            question=flag.get("QUESTION"),
            ans=flag.get("ANSWER"),
            wrong="\n".join(flag.get("WRONG")),
        ),
        reply_markup=markup,
    )
    logger.info("Private: Detail question")
    logger.debug(callback_query)
    return DETAIL_VIEW


def save_private(context, callback_query):
    save_config(context.bot_data.get("config"), filename)
    context.chat_data.clear()
    keyboard = [
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("BACK"), callback_data="back"
            )
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    callback_query.edit_message_text(
        reload_config(context),
        reply_markup=markup,
    )
    logger.info(f"Private: Saved config")
    logger.debug(context.bot_data.get("config"))


@collect_error
def delete_question_private(update, context):
    callback_query = update.callback_query
    callback_query.answer()
    callback_query.edit_message_text(
        context.bot_data.get("config").get("DELETING_PRIVATE")
    )
    num = private_callback(callback_query.data)
    tile = context.bot_data.get("config").get("CHALLENGE").pop(num)
    logger.info(f"Private: Delete question {tile}")
    save_private(context, callback_query)
    return DETAIL_VIEW


@collect_error
def edit_question_private(update, context):
    message = update.message
    callback_query = update.callback_query
    if callback_query:
        text = "Begin"
        callback_query.answer()
        index = private_callback(callback_query.data)
        context.chat_data.clear()
        context.chat_data.update(index=index)
        callback_query.edit_message_text(
            context.bot_data.get("config")
            .get("EDIT_QUESTION_PRIVATE")
            .format(num=index + 1)
        )
    elif message:
        text = message.text
        if not context.chat_data.get("QUESTION"):
            context.chat_data.update(QUESTION=text)
            return_text = (
                context.bot_data.get("config")
                .get("EDIT_ANSWER_PRIVATE")
                .format(text=text)
            )
        elif not context.chat_data.get("ANSWER"):
            context.chat_data.update(ANSWER=text)
            return_text = (
                context.bot_data.get("config")
                .get("EDIT_WRONG_PRIVATE")
                .format(text=text)
            )
        else:
            if not context.chat_data.get("WRONG"):
                context.chat_data["WRONG"] = list()
            context.chat_data.get("WRONG").append(text)
            return_text = (
                context.bot_data.get("config")
                .get("EDIT_MORE_WRONG_PRIVATE")
                .format(text=text)
            )
        message.reply_text(
            context.bot_data.get("config").get("EDIT_PRIVATE").format(text=return_text)
        )
        logger.info(f"Private: Edit question {text}")
    return QUESTION_EDIT


@collect_error
def finish_edit_private(update, context):
    message = update.message
    if not context.chat_data.get("WRONG"):
        message.reply_text(context.bot_data.get("config").get("EDIT_UNFINISH_PRIVATE"))
        return QUESTION_EDIT
    index = context.chat_data.get("index")
    keyboard = [
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("SAVE_QUESTION_BTN"),
                callback_data="save",
            )
        ],
        [
            InlineKeyboardButton(
                context.bot_data.get("config").get("REEDIT_QUESTION_BTN"),
                callback_data=f"edit_question_private|{index}",
            )
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    message.reply_text(
        "\n".join(
            [
                context.bot_data.get("config")
                .get("EDIT_FINISH_PRIVATE")
                .format(num=index + 1),
                context.bot_data.get("config")
                .get("DETAIL_QUESTION_PRIVATE")
                .format(
                    question=context.chat_data.get("QUESTION"),
                    ans=context.chat_data.get("ANSWER"),
                    wrong="\n".join(context.chat_data.get("WRONG")),
                ),
            ]
        ),
        reply_markup=markup,
    )
    logger.info(f"Private: Finish edit {context.chat_data}")
    return DETAIL_VIEW


@collect_error
def save_question_private(update, context):
    callback_query = update.callback_query
    callback_query.answer()
    callback_query.edit_message_text(
        context.bot_data.get("config").get("SAVING_PRIVATE")
    )
    if context.chat_data:
        index = (
            context.chat_data.pop("index")
            if "index" in context.chat_data
            else len(context.bot_data.get("config").get("CHALLENGE"))
        )
        if index < len(context.bot_data.get("config").get("CHALLENGE")):
            context.bot_data.get("config").get("CHALLENGE")[
                index
            ] = context.chat_data.copy()
        else:
            context.bot_data.get("config").get("CHALLENGE").append(
                context.chat_data.copy()
            )
        logger.info(f"Private: Saving question {context.chat_data}")
    save_private(context, callback_query)
    return DETAIL_VIEW


@collect_error
def cancel_private(update, context):
    message = update.message
    context.chat_data.clear()
    message.reply_text(context.bot_data.get("config").get("CANCEL_PRIVATE"))
    logger.info(f"Private: Cancel")
    return ConversationHandler.END


@collect_error
def config_private(update, context):
    message = update.message
    with open(filename, "rb") as file:
        message.reply_document(file)
    logger.info(f"Private: Config")
    return ConversationHandler.END


@collect_error
def config_file_private(update, context):
    message = update.effective_message
    file_id = message.document.file_id
    filestream = updater.bot.get_file(file_id)
    if filestream:
        file = BytesIO()
        filestream.download(out=file)
        logger.info(f"Private: Config file successfully downloaded {file_id}")
        try:
            with file:
                test = load_yml(file.getvalue())
            config = load_config(test, check_token=False)
            save_config(config, filename)
            message.reply_text(reload_config(context))
        except Exception as err:
            logger.error(err)
            message.reply_text(
                context.bot_data.get("config").get("CORRUPT").format(text=err.__str__())
            )


def reload_config(context):
    for job in context.job_queue.get_jobs_by_name("reload"):
        job.schedule_removal()
    if jobs := [t.id for t in context.job_queue.scheduler.get_jobs()]:
        context.job_queue.run_once(
            reload_config, context.bot_data.get("config").get("TIME"), name="reload"
        )
        logger.info(f"Job reload: Waiting for {jobs}")
        return context.bot_data.get("config").get("PENDING")
    else:
        try:
            yaml = load_yml_path(filename)
            context.bot_data.update(config=load_config(yaml, check_token=False))
            logger.info(f"Job reload: Successfully reloaded {filename}")
            return (
                context.bot_data.get("config")
                .get("RELOAD")
                .format(num=len(context.bot_data.get("config").get("CHALLENGE")))
            )
        except Exception as err:
            logger.error(err)
            return (
                context.bot_data.get("config").get("CORRUPT").format(text=err.__str__())
            )


def save_config(config, name=None):
    save = copy.deepcopy(config)
    if not name:
        name = f"{filename}.bak"
    for flag in save.get("CHALLENGE"):
        if flag.get("answer"):
            flag.pop("answer")
        if flag.get("wrong"):
            flag.pop("wrong")
        if flag.get("index"):
            flag.pop("index")
    save["TOKEN"] = updater.bot.token
    with open(name, "w") as file:
        save_yml(save, file)
    logger.info(f"Config: Dumped {name}")
    logger.debug(save)


if __name__ == "__main__":
    filename = (
        sys.argv[1]
        if len(sys.argv) >= 2 and os.path.exists(sys.argv[1])
        else "config.yml"
    )
    yaml = load_yml_path(filename)
    config = load_config(yaml)
    command = list()
    pkfile = f"{filename}.pickle"
    if os.path.isfile(pkfile):
        os.remove(pkfile)
        # try:
        # with open(pkfile, "rb") as f:
        #     pickle.load(f)
        # except Exception as err:
        #     logger.exception(err)
        #     os.remove(pkfile)
    # pk = PicklePersistence(filename=pkfile, on_flush=True)
    updater = Updater(
        config.get("TOKEN"),
        # persistence=pk,
    )
    save_config(config)
    updater.dispatcher.bot_data.update(config=config)
    updater.dispatcher.add_handler(
        CommandHandler("start", start_command, filters=Filters.group)
    )
    chatfilter = Filters.chat(config.get("CHAT")) if config.get("CHAT") else None
    updater.dispatcher.add_handler(
        MessageHandler(
            MergedFilter(Filters.status_update.new_chat_members, and_filter=chatfilter),
            newmem,
            run_async=True,
        )
    )
    updater.dispatcher.add_handler(CallbackQueryHandler(query, pattern=r"^challenge\|"))
    updater.dispatcher.add_handler(CallbackQueryHandler(admin, pattern=r"^admin\|"))
    if config.get("CHAT"):
        try:
            updater.bot.get_chat_administrators(config.get("CHAT"))
        except BadRequest as err:
            logger.error(err)
        else:
            CHOOSING, LIST_VIEW, DETAIL_VIEW, QUESTION_EDIT = range(4)
            conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("start", start_private, filters=Filters.private)
                ],
                states={
                    CHOOSING: [
                        CallbackQueryHandler(save_question_private, pattern=r"^save$"),
                        CallbackQueryHandler(
                            edit_question_private, pattern=r"^edit_question_private"
                        ),
                        CallbackQueryHandler(
                            list_question_private, pattern=r"^list_question_private"
                        ),
                    ],
                    LIST_VIEW: [
                        CallbackQueryHandler(start_private, pattern=r"^back$"),
                        CallbackQueryHandler(
                            detail_question_private, pattern=r"^detail_question_private"
                        ),
                    ],
                    DETAIL_VIEW: [
                        CallbackQueryHandler(save_question_private, pattern=r"^save$"),
                        CallbackQueryHandler(list_question_private, pattern=r"^back$"),
                        CallbackQueryHandler(
                            delete_question_private, pattern=r"^delete_question_private"
                        ),
                        CallbackQueryHandler(
                            edit_question_private, pattern=r"^edit_question_private"
                        ),
                    ],
                    QUESTION_EDIT: [
                        MessageHandler(
                            Filters.text & ~Filters.command, edit_question_private
                        ),
                        CommandHandler("finish", finish_edit_private),
                    ],
                },
                fallbacks=[
                    CommandHandler("cancel", cancel_private),
                    CommandHandler("config", config_private),
                    CommandHandler("reload", reload_private),
                    MessageHandler(
                        Filters.document,
                        config_file_private,
                    ),
                ],
                name="setting",
                allow_reentry=True,
                # persistent=True,
            )
            updater.dispatcher.add_handler(conv_handler)
            logger.info("Enhanced admin control enabled for private chat.")
    if config.get("QUIZ"):
        updater.dispatcher.add_handler(
            CommandHandler("quiz", quiz_command, filters=chatfilter)
        )
        command.append(["quiz", config.get("QUIZ")])
        logger.info("Quiz command registered.")
    if config.get("ADMIN"):
        updater.dispatcher.add_handler(
            CommandHandler("admin", admin_command, filters=chatfilter)
        )
        command.append(["admin", config.get("ADMIN")])
        logger.info("Admin command registered.")
    if (DOMAIN := os.environ.get("DOMAIN")) and (TOKEN := config.get("TOKEN")):
        updater.start_webhook(
            listen="0.0.0.0", port=int(os.environ.get("PORT", 8080)), url_path=TOKEN
        )
        updater.bot.setWebhook(DOMAIN + TOKEN)
    else:
        updater.start_polling()
    logger.info(f"Bot @{updater.bot.get_me().username} started.")
    updater.bot.set_my_commands(command)
    updater.idle()
