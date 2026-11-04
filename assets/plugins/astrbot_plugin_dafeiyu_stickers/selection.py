"""Bounded, abstaining sticker decisions without another model invocation.

The primary model may select an offered ID or explicitly abstain. A missing
decision is different from abstention: only clear, mutually matching everyday
speech acts may use the local fallback. No emoji, inferred personality or image
description alone is evidence about the user's current emotion.
"""
from __future__ import annotations

import re

from . import library_state

NONE = {"none", "skip", "no_sticker", "无", "不发", "不配图"}
NEGATIVE = re.compile(r"不开心|不高兴|不喜欢|讨厌|失败|没(?:有)?通过|没考上|坏消息|别高兴|不要庆祝|哭|难过|伤心|痛苦|焦虑|失望")
ABSTAIN = re.compile(r"(?:先|只)(?:用|回)?文字|不(?:发|加|配)(?:送)?(?:表情|图)|不适合(?:发|加|配)")
META_DISCUSSION = re.compile(r"意思|含义|解释|为什么|如何|怎么理解|怎么看待|文案|翻译|举例|引用|[他她]说|模型|插件|功能|API|测试|(?:能|可以)(?:不能|不可以)?说|[\"“”‘’]")
FAMILIES = (
    ("sleep", r"晚安|睡啦|睡了|睡觉|困了|好困", r"晚安|好梦|睡|休息", ("sleepy",)),
    ("affection", r"喜欢你|想你|爱你|抱抱|贴贴|亲亲|摸摸|好可爱|真可爱|可爱的小鱼", r"喜欢|想你|抱抱|贴贴|害羞|脸红|心动|谢谢|开心|可爱", ("love", "shy")),
    ("celebration", r"好耶|太棒|好开心|很开心|真开心|高兴|成功了|通过了|考上了|完成啦|完成了|升职|中奖", r"好耶|太棒|开心|高兴|恭喜|庆祝|棒棒|耶[！!～~]|真好", ("happy",)),
    ("greeting", r"(?:^|[\s，,。!！])(?:你好|早安|早上好|晚上好|嗨|hello|hi)(?:呀|啊|小鱼|大肥鱼|[\s，,。!！～~]|$)|问候", r"你好|早安|早上好|晚上好|嗨|hello|欢迎|见到你|来啦", ("happy", "daily")),
    ("playful", r"哈哈|嘿嘿|摸鱼|撒娇|开玩笑|逗你|接梗", r"哈哈|嘿嘿|摸鱼|撒娇|接住|笑|逗", ("daily", "happy")),
)


def trim_mascot_tail(text):
    """Remove a standalone fish sign-off only when a real image replaces it.

    Inline semantic uses such as '想吃🐟' remain intact. The caller excludes
    code/link/tool replies and only applies this to the last visible Plain.
    """
    if re.fullmatch(r"\s*[🐟🐳🐋]\ufe0f?\s*", text):
        return ""
    return re.sub(r"([。！!？?～~]\s*|\s+)[🐟🐳🐋]\ufe0f?\s*$", r"\1", text).rstrip()


def fallback_emotions(incoming, reply):
    if (not isinstance(incoming, str) or not isinstance(reply, str) or not reply.strip()
            or len(incoming) > 160 or len(reply) > 400 or META_DISCUSSION.search(incoming)
            or NEGATIVE.search(incoming + "\n" + reply) or ABSTAIN.search(reply)):
        return ()
    for _, left, right, emotions in FAMILIES:
        if re.search(left, incoming, re.I) and re.search(right, reply, re.I):
            if emotions == ("love", "shy") and re.search(r"害羞|脸红", reply):
                return ("shy", "love")
            return emotions
    return ()


def decide(items, markers, incoming, reply, *, avoid="", index=0):
    """Return one offered item and an explicit, privacy-safe reason code.

    Any valid none decision beats every image marker. Invalid markers never
    grant fallback permission. Explicit negative scene feedback is not guessed
    from silence; it excludes automatic choices until the user clears it.
    """
    values = [value.strip().casefold() for value in markers if isinstance(value, str)]
    if any(value in NONE for value in values) or ABSTAIN.search(reply):
        return None, "model_declined"
    eligible = [row for row in items if row.get("enabled", True) is not False and not row.get("scene_rejections", 0)]
    if values:
        for value in values:
            exact = next((row for row in eligible if row["id"].casefold() == value), None)
            if exact and value not in {"happy", "sad", "angry", "shy", "sleepy", "love", "confused", "daily"}:
                return (exact, "selected_model") if exact["id"] != avoid else (None, "repeat_avoided")
            moods = {row["emotion"] for row in eligible if value == row["emotion"] or value in {
                str(row.get("title") or row.get("label", "")).casefold(), *[x.casefold() for x in row.get("aliases", [])]}}
            matches = [row for row in eligible if row["emotion"] in moods and row["id"] != avoid]
            if matches:
                return matches[index % len(matches)], "selected_model"
        return None, "invalid_marker"
    emotions = fallback_emotions(incoming, reply)
    if not emotions:
        return None, "marker_missing_no_match"
    ranked = library_state.lexical_rank(eligible, incoming + "\n" + reply, avoid=avoid, matches_only=True)
    for mood in emotions:
        matches = [row for row in ranked if row["emotion"] == mood and row["id"] != avoid]
        if matches:
            # Rotate equally appropriate local choices without using the fish
            # emoji as a mood label or overriding a rejected scene.
            return matches[index % len(matches)], "selected_fallback"
    return None, "marker_missing_no_match"
