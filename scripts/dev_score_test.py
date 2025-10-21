import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from src.adapters.llm_scoring import call_relevance_api, parse_score
from src.config import get_settings

s = get_settings()
print('Model:', s.score_model_name)
text = '''请判断下面的内容和教育的相关程度，输出{0-100}，其中0表示完全不相关，100表示完全相关，直接输出数字即可：
重罚！成都凌晨连查2台危货非法运输车辆 涉案企业系半年内二次被查
成都市交通运输综合行政执法总队在专项行动中查获了两台非法运输危险化学品的车辆，这些车辆虽所属企业有资质，但车辆本身无资质，且涉案运输企业半年内第二次被查。执法人员通过精准打击，发现这些车辆在凌晨和深夜试图躲避检查，实际运输无水甲醇、乙腈、冰乙酸等危险化学品。尽管运输公司持有有效的《道路危险货物运输许可证》，但涉案车辆未取得危险货物运输《道路运输证》，属于典型的“借合规企业之名，行违法运输之实”。此前，该企业在7月31日也曾因相同违法行为被查处。成都市交通运输综合行政执法总队表示，将加大危货运输领域的监管力度，织密城市交通安全防护网，保障城市交通运输秩序与公共安全，并提醒所有危货运输从业者合规经营，切勿心存侥幸。（人民网）'''
raw = call_relevance_api(text)
print('Raw:', raw)
print('Parsed:', parse_score(raw))
