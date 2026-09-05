# -*- coding: utf-8 -*-
"""species.py — 物种档案：各类型宠物的适用记录、常见疾病、该做与不该做的事。

单一数据源：前端表单过滤、详情页护理要点卡、后端校验、AI 工具 get_care_guide
全部引用本模块，避免多处维护导致口径不一致。
"""
import json

# 每个物种：
#   record_types 适用的健康记录类型（key 与 db.RECORD_TYPES 对应）
#   diseases      常见疾病/病症快捷标签（前端一键填入标题；鱼类刻意不含"腹泻"）
#   care_do       该做的事（日常护理规范）
#   care_dont     不该做的事（禁忌与高风险行为）
SPECIES = {
    "dog": {
        "label": "狗",
        "record_types": ["vaccine", "checkup", "deworm", "medication", "clinic"],
        "diseases": ["犬瘟热", "细小病毒", "皮肤病", "耳螨", "肠胃炎", "腹泻", "感冒"],
        "care_do": [
            "每年接种狂犬疫苗，按兽医建议完成联苗免疫",
            "每月体外驱虫、每 3 个月体内驱虫",
            "每日遛狗运动，保持规律作息",
            "定期洗澡、梳毛与牙齿清洁",
            "夏季避开高温时段外出，防止中暑",
        ],
        "care_dont": [
            "严禁喂食巧克力、葡萄、洋葱、木糖醇等有毒食物",
            "剧烈运动后不宜立即大量饮水",
            "不宜长期关笼、缺乏运动与社交",
            "人类药物不可随意喂服（对乙酰氨基酚对狗有剧毒）",
        ],
    },
    "cat": {
        "label": "猫",
        "record_types": ["vaccine", "checkup", "deworm", "medication", "clinic"],
        "diseases": ["猫鼻支", "尿闭", "泌尿系统疾病", "毛球症", "猫癣", "口炎", "腹泻"],
        "care_do": [
            "按免疫程序接种猫三联并定期加强",
            "每月驱虫，室内猫同样不能省略",
            "每日清理猫砂盆，观察排泄物是否异常",
            "多放置水碗或使用流动饮水器，预防泌尿疾病",
            "定期梳毛，换毛期辅助排毛球",
        ],
        "care_dont": [
            "百合花对猫剧毒，家中切勿摆放",
            "禁喂洋葱、大蒜、巧克力；不宜喂纯牛奶（乳糖不耐受）",
            "不宜频繁洗澡（应激且破坏皮肤屏障）",
            "人类药物不可随意喂服（对乙酰氨基酚对猫致命）",
        ],
    },
    "bird": {
        "label": "鸟",
        "record_types": ["checkup", "medication", "clinic"],
        "diseases": ["感冒", "嗉囊炎", "啄羽症", "羽虱", "拉稀"],
        "care_do": [
            "定期清洁鸟笼与食水器具，保持通风",
            "提供浅水盆供其水浴，羽粉多的种类可喷雾",
            "食物多样搭配，补充蔬果与矿物墨鱼骨",
            "远离厨房油烟，保持环境温度稳定",
            "留意粪便状态，异常及时就诊",
        ],
        "care_dont": [
            "牛油果对鸟类剧毒，严禁喂食",
            "香水、杀虫剂、不粘锅高温烟气可致鸟猝死，务必远离",
            "不宜长期只喂单一谷物（营养失衡）",
            "宠物鸟一般不接种常规疫苗，勿轻信「鸟类疫苗」推销",
        ],
    },
    "fish": {
        "label": "鱼",
        "record_types": ["checkup", "medication", "clinic"],
        "diseases": ["白点病", "烂鳍烂尾", "水霉病", "肠炎", "缺氧", "鳃部寄生虫"],
        "care_do": [
            "每周换水约三分之一，新水需困水除氯",
            "监控水温与水质，避免温度骤变",
            "定时定量喂食，以几分钟内吃完为宜",
            "新鱼入缸先过水检疫，避免带入病原",
            "观察游姿与体表，发现白点、烂鳍及时处理",
        ],
        "care_dont": [
            "自来水含氯，不可不经除氯直接入缸",
            "不可喂食过量——残饵败坏水质是鱼病首因",
            "不宜频繁、大量换水或彻底清洗滤材",
            "习性冲突的鱼不宜混养（撕咬、追尾会致病）",
        ],
    },
    "other": {
        "label": "其他宠物",
        "record_types": ["checkup", "medication", "clinic"],
        "diseases": ["食欲下降", "精神萎靡", "体表异常", "外伤"],
        "care_do": [
            "保持环境温度与湿度稳定",
            "每日观察食欲、精神与排泄情况",
            "记录异常行为，就诊时提供给医生",
        ],
        "care_dont": [
            "避免温度骤变与惊扰",
            "不确定的食物一律不喂，异常行为及时就医",
        ],
    },
}

FALLBACK = "other"


def profile(pet_type: str | None) -> dict:
    """取物种档案，未知类型回退到通用档案。"""
    return SPECIES.get((pet_type or "").strip(), SPECIES[FALLBACK])


def type_label(pet_type: str | None) -> str:
    return profile(pet_type)["label"]


def allowed_record_types(pet_type: str | None) -> list[str]:
    return profile(pet_type)["record_types"]


def record_type_allowed(pet_type: str | None, record_type: str) -> tuple[bool, str]:
    """校验记录类型是否适用；返回 (是否允许, 不允许时的提示)。"""
    if record_type in allowed_record_types(pet_type):
        return True, ""
    label = type_label(pet_type)
    allowed = "、".join(profile(pet_type)["record_types"])
    return False, f"「{label}」不适用该记录类型（可用：{allowed}）"


def care_guide_text(pet_type: str | None, pet_name: str = "") -> str:
    """把物种档案拼成给 AI 工具/用户看的文本。"""
    sp = profile(pet_type)
    head = f"「{pet_name}」的物种护理规范（{sp['label']}）：" if pet_name else f"{sp['label']}护理规范："
    lines = [head]
    lines.append("- 适用的健康记录类型：" +
                 "、".join(sp["record_types"]) +
                 "；其余类型（如疫苗/驱虫）不适用于该物种。")
    lines.append("- 该做的事：" + "；".join(sp["care_do"]) + "。")
    lines.append("- 不该做的事（禁忌）：" + "；".join(sp["care_dont"]) + "。")
    lines.append("- 该物种常见疾病：" + "、".join(sp["diseases"]) + "。")
    lines.append("- 提醒：回答时遵守上述物种边界，不要把不适用的病症或处置安到该物种上；涉及医疗判断以兽医意见为准。")
    return "\n".join(lines)


def api_payload() -> dict:
    """给前端的物种档案（JSON 友好）。"""
    return json.loads(json.dumps(SPECIES, ensure_ascii=False))
