"""制度查询日期门禁：只接受可从用户原话确定的日期，防止模型默认为错误年份。"""

import re
from datetime import date, timedelta

# 限定当前支持的明确日历格式；年/月区间不擅自选择其中一天。
EXACT_DATE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?!\d)")
AMBIGUOUS_TIME = re.compile(
    r"去年|前年|明年|今年|上个?月|下个?月|本月|这个月|上周|下周|以前|之前|旧制度|旧版|历史|"
    r"当时|\d{4}年|\d{4}[-/]\d{1,2}(?![-/\d])"
)


def policy_date(message: str, proposed: date | None, today: date) -> tuple[bool, date | None]:
    """返回（日期是否明确，检索日期）；None 表示用户未指定，使用业务当天。

    此函数只在 policy 分支执行，不干预报销发生日期；模型提供的日期必须能从原话核对。
    支持唯一完整日期和今天 / 昨天 / 前天 / 明天 / 后天；不支持的时间需用户写明日期。
    """
    exact = EXACT_DATE.findall(message)
    if exact:
        try:
            dates = {date(int(year), int(month), int(day)) for year, month, day in exact}
        except ValueError:
            return False, None
        if len(dates) != 1:
            return False, None
        return True, dates.pop()
    relative = {
        today + timedelta(days=offset)
        for word, offset in [("今天", 0), ("昨天", -1), ("前天", -2), ("明天", 1), ("后天", 2)]
        if word in message
    }
    if AMBIGUOUS_TIME.search(message) or len(relative) > 1:
        return False, None
    if relative:
        return True, relative.pop()
    if proposed is not None:
        # 模型识别到本实现无法核对的日期表达时，要求改写而不是接受模型猜测。
        return False, None
    return True, None
