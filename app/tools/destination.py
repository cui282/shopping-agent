from __future__ import annotations

SUPPORTED_DESTINATION = "中国大陆"

_MAINLAND_DESTINATION_ALIASES = {
    "中国",
    "中国内地",
    "中国大陆",
    "中国大陆地区",
    "大陆",
    "大陆地区",
    "国内",
    "北京",
    "北京市",
    "上海",
    "上海市",
    "天津",
    "天津市",
    "重庆",
    "重庆市",
    "河北",
    "河北省",
    "山西",
    "山西省",
    "辽宁",
    "辽宁省",
    "吉林",
    "吉林省",
    "黑龙江",
    "黑龙江省",
    "江苏",
    "江苏省",
    "浙江",
    "浙江省",
    "安徽",
    "安徽省",
    "福建",
    "福建省",
    "江西",
    "江西省",
    "山东",
    "山东省",
    "河南",
    "河南省",
    "湖北",
    "湖北省",
    "湖南",
    "湖南省",
    "广东",
    "广东省",
    "广西",
    "广西壮族自治区",
    "海南",
    "海南省",
    "四川",
    "四川省",
    "贵州",
    "贵州省",
    "云南",
    "云南省",
    "西藏",
    "西藏自治区",
    "陕西",
    "陕西省",
    "甘肃",
    "甘肃省",
    "青海",
    "青海省",
    "宁夏",
    "宁夏回族自治区",
    "新疆",
    "新疆维吾尔自治区",
    "内蒙古",
    "内蒙古自治区",
    "深圳",
    "深圳市",
    "广州",
    "广州市",
    "杭州",
    "杭州市",
    "南京",
    "南京市",
    "苏州",
    "苏州市",
    "成都",
    "成都市",
    "武汉",
    "武汉市",
    "西安",
    "西安市",
    "厦门",
    "厦门市",
    "青岛",
    "青岛市",
    "大连",
    "大连市",
}

_MAINLAND_DESTINATION_PREFIXES = tuple(
    sorted(
        {
            alias
            for alias in _MAINLAND_DESTINATION_ALIASES
            if alias
            not in {
                "中国",
                "中国内地",
                "中国大陆",
                "中国大陆地区",
                "大陆",
                "大陆地区",
                "国内",
            }
        },
        key=len,
        reverse=True,
    )
)


def normalize_destination(value: str) -> str:
    destination = value.strip().strip(" :：")
    is_mainland_address = any(
        destination.startswith(prefix) for prefix in _MAINLAND_DESTINATION_PREFIXES
    )
    if (
        destination in _MAINLAND_DESTINATION_ALIASES
        or destination.startswith("中国大陆")
        or is_mainland_address
    ):
        return SUPPORTED_DESTINATION
    return destination


def is_supported_destination(value: str) -> bool:
    return normalize_destination(value) == SUPPORTED_DESTINATION


class UnsupportedDestinationError(ValueError):
    def __init__(self, destination: str) -> None:
        self.destination = destination
        super().__init__(f"only {SUPPORTED_DESTINATION} is supported, received {destination}")
