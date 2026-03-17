import json
import os
import random
from datetime import datetime

"""
generate_mock_data.py

Generates realistic mock customer/admin Q&A pairs for business/finance/investment support.
Output: data/conversations.json

Design goals:
- Thai as primary language with natural English investment jargon mixed in.
- Diverse intents and phrasings (not repetitive templates).
- Investment/business/finance only.
"""

SCENARIOS = {
    "options": [
        "Option คืออะไร มือใหม่ขอพื้นฐานหน่อยครับ",
        "Call vs Put ต่างกันยังไงครับ",
        "Premium ของ Option คิดจากอะไร (IV, time value) ?",
        "Theta decay คืออะไร ทำไมใกล้หมดอายุแล้ว premium ไหลเร็ว",
        "Delta / Gamma / Vega ต้องดูยังไงในการเทรด Options",
        "ITM / ATM / OTM ต่างกันยังไง เลือก strike ยังไงดี",
        "อยากทำ covered call ต้องถือหุ้นก่อนใช่ไหม",
        "Short option ต้องวาง margin เท่าไหร่ และเสี่ยงอะไรบ้าง",
        "Exercise/Assignment คืออะไร เกิดตอนไหน",
        "ทำไมบางซีรีส์ bid/ask กว้างและสภาพคล่องต่ำ",
        "อยาก hedging พอร์ตด้วย Put ต้องเริ่มยังไง",
        "Long straddle/strangle เหมาะกับช่วงข่าวแรง ๆ ใช่ไหม",
        "Option chain ดูตรงไหน มี Greeks/IV แสดงไหม",
        "Set50 Options หมดอายุวันไหน และมี roll ได้ไหม",
        "ถ้าถือ option จน expire จะเกิดอะไรขึ้น (auto expire/settlement)",
    ],
    "futures_tfex": [
        "Futures คืออะไร ต่างจากหุ้นยังไงครับ",
        "Initial margin / Maintenance margin คืออะไร",
        "โดน margin call ต้องเติมเงินภายในกี่โมง",
        "Mark to Market คืออะไร ทำไมยอดเงินเปลี่ยนทุกวัน",
        "Leverage ใน TFEX ประมาณกี่เท่า และควรคุม risk ยังไง",
        "Long/Short เปิดสถานะยังไง และปิดทำกำไรทำยังไง",
        "Force sell/forced close เกิดจากอะไร ป้องกันยังไง",
        "Series ของสัญญา (H/M/U/Z) ดูยังไงว่าใกล้หมดอายุ",
        "Roll over คืออะไร ทำในวันไหนถึงจะเหมาะ",
        "Gold Futures vs Gold Online ต่างกันตรงไหน",
        "USD Futures ใช้ hedge ค่าเงินได้ยังไงแบบ practical",
        "ค่าธรรมเนียม TFEX ต่อสัญญา + ค่าธรรมเนียมตลาดประมาณเท่าไหร่",
        "Bid/Offer ในกระดาน futures บางทีหาย เกิดจากอะไร",
    ],
    "stocks_fundamentals": [
        "XD คืออะไร ถ้าซื้อก่อน XD 1 วัน ยังได้ปันผลไหม",
        "Dividend yield ดูยังไง และต่างจาก payout ratio ไหม",
        "P/E สูงแปลว่าแพงเสมอไหม ต้องเทียบ growth ยังไง",
        "P/BV สำหรับหุ้นแบงก์ควรดูยังไง",
        "EPS diluted vs basic ต่างกันไหม",
        "Market cap คำนวณยังไง และ free float สำคัญแค่ไหน",
        "Cash balance / marginable / short selling เช็คตรงไหน",
        "SP/NP คืออะไร กระทบการซื้อขายยังไง",
        "NVDR คืออะไร เหมาะกับต่างชาติหรือคนไทยก็ใช้ได้",
        "RO/Right offering คืออะไร ถ้าไม่ใช้สิทธิ์จะเป็นยังไง",
        "Warrant / DW ต่างกันยังไง และมี time decay ไหม",
        "งบการเงินดูเบื้องต้นต้องเริ่มจากงบไหน (IS/BS/CF)",
        "ทำไมราคาหุ้นชน ceiling/floor แล้วซื้อไม่ได้",
    ],
    "portfolio_strategy": [
        "DCA คืออะไร เหมาะกับหุ้นหรือกองทุนมากกว่ากัน",
        "Asset allocation ทำยังไงให้เหมาะกับความเสี่ยง (risk profile)",
        "Stop loss ควรตั้งกี่ % และต้องดู volatility ไหม",
        "Take profit vs trailing stop ใช้ต่างกันยังไง",
        "Value investing vs growth investing ต่างกันยังไง",
        "Technical analysis เริ่มจาก indicator อะไรดี (MA/RSI/MACD)",
        "Money management คืออะไร ควร risk per trade กี่ %",
        "Bear market ควรจัดพอร์ตยังไง (defensive, cash, hedge)",
        "Backtest กลยุทธ์ทำยังไงให้ไม่ bias (lookahead/overfit)",
        "Margin of safety คืออะไร และคำนวณแบบง่าย ๆ ได้ไหม",
        "Hedging คืออะไร ใช้ futures/options ป้องกันพอร์ตได้จริงไหม",
    ],
    "trading_platform": [
        "ตั้ง Stop order / Stop limit ในแอปเทรดยังไงครับ",
        "ตั้ง alert แจ้งเตือนราคา/volume ในแอปได้ไหม",
        "ดูกราฟแบบ candlestick แล้วเพิ่ม RSI/MACD ยังไง",
        "ดู Bid/Offer (Level 2) ได้จากเมนูไหน",
        "ประวัติการซื้อขายย้อนหลัง/statement export ทำยังไง",
        "ส่งคำสั่งแล้วขึ้น Rejected ต้องเช็คอะไรบ้าง (cash, buying power, circuit breaker)",
        "ดูพอร์ตหุ้น vs พอร์ต TFEX สลับยังไงในหน้าเดียว",
        "ตั้งค่า default order size และ validity (DAY/IOC) ได้ไหม",
    ],
}

OPENERS = [
    "สวัสดีครับ", "สวัสดีค่ะ", "ขอสอบถามครับ", "รบกวนสอบถามหน่อยครับ", "สอบถามหน่อยค่ะ", "ขอถามนิดนึงครับ"
]

CLOSERS = [
    "ครับ", "ค่ะ", "ขอบคุณครับ", "ขอบคุณค่ะ", "รบกวนด้วยครับ", "ช่วยแนะนำหน่อยค่ะ"
]

def _pick(xs):
    return random.choice(xs)

def _mix_jargon(text: str) -> str:
    """Lightly mixes natural English jargon into Thai phrasing."""
    additions = [
        "แบบ practical", "แบบ step-by-step", "ใน app", "ในระบบ", "ในพอร์ต", "เชิง risk management",
        "เรื่อง liquidity", "เรื่อง slippage", "เช็คใน statement", "ดูใน order book",
    ]
    if random.random() < 0.35:
        return f"{text} {_pick(additions)}"
    return text

ANSWER_BANK = {
    "options": [
        "Options คือสัญญาที่ให้ “สิทธิ” (ไม่ใช่ข้อบังคับ) ในการซื้อ/ขายสินทรัพย์อ้างอิงที่ราคา Strike ภายในเวลาที่กำหนด โดยราคาที่จ่ายเรียกว่า Premium ครับ",
        "Premium ของ option จะขึ้นกับหลายปัจจัย เช่น ราคาอ้างอิง, Strike, เวลา (time to expiry), และ Implied Volatility (IV) ค่ะ",
        "Greeks (Delta/Gamma/Vega/Theta) ใช้ประเมินความไวของราคา option ต่อการเปลี่ยนแปลงของราคาอ้างอิง เวลา และ volatility ครับ",
        "การ short option มีความเสี่ยงสูงและต้องวางหลักประกัน (margin) ตามเกณฑ์ตลาด/โบรกเกอร์ แนะนำให้เริ่มจากความเข้าใจ payoff และคุม position size ค่ะ",
        "ถ้าถือ option จนหมดอายุ (expiry) แล้วเป็น OTM มักจะหมดค่าทันที ส่วน ITM จะมีขั้นตอน settlement/assignment ตามเงื่อนไขของสัญญา ครับ",
    ],
    "futures_tfex": [
        "Futures เป็นสัญญาซื้อ/ขายล่วงหน้าที่มีการวางหลักประกัน (margin) และมีการ Mark-to-Market รายวัน ทำให้กำไร/ขาดทุนสะท้อนในบัญชีทุกวันครับ",
        "Initial margin คือเงินประกันเริ่มต้น ส่วน Maintenance margin คือระดับขั้นต่ำ ถ้าต่ำกว่าเกณฑ์จะเกิด Margin Call และต้องเติมเงินเพิ่มค่ะ",
        "การ roll over คือการปิดสัญญาเดิมแล้วเปิดสัญญา series ใหม่ เพื่อเลี่ยงความเสี่ยงช่วงใกล้หมดอายุ/สภาพคล่องลดลงครับ",
        "Long คือคาดว่าราคาเพิ่มขึ้น, Short คือคาดว่าราคาลดลง ควรกำหนด stop loss และจำกัดความเสี่ยงต่อครั้งให้ชัดเจนค่ะ",
    ],
    "stocks_fundamentals": [
        "XD คือวันที่ขึ้นเครื่องหมายไม่รับสิทธิปันผล/สิทธิอื่น ๆ โดยหลักแล้วต้องซื้อให้ทันตามเงื่อนไขวันกำหนดรายชื่อผู้ถือหุ้น (Record date) ครับ",
        "P/E ต้องดูร่วมกับ growth และคุณภาพกำไร (earnings quality) ไม่ได้แปลว่าแพง/ถูกแบบตายตัวค่ะ",
        "Cash balance, short selling, และสถานะหลักประกัน มักตรวจสอบได้จากประกาศตลาดหรือเมนูข้อมูลหลักทรัพย์ในแอปเทรดครับ",
        "RO/Warrant/DW มีเงื่อนไขและความเสี่ยงต่างกัน โดยเฉพาะ DW/Option-like products มักมี time decay และ sensitivity ต่อ IV ค่ะ",
    ],
    "portfolio_strategy": [
        "DCA คือการทยอยลงทุนเป็นงวด ๆ เพื่อลดความเสี่ยงจากการเข้าซื้อผิดจังหวะ เหมาะกับการลงทุนระยะยาวและสินทรัพย์ที่มีความผันผวนครับ",
        "Asset allocation คือการกระจายสัดส่วนสินทรัพย์ (หุ้น/ตราสารหนี้/ทอง/เงินสด) ให้สอดคล้องกับเป้าหมายและระดับความเสี่ยงที่รับได้ค่ะ",
        "Stop loss ควรกำหนดจากระดับความเสี่ยงต่อครั้ง (risk per trade) และ volatility ไม่ใช่ตั้งแบบสุ่ม ๆ ครับ",
        "Backtest ควรระวัง lookahead bias, survivorship bias และ overfitting โดยใช้ข้อมูลนอกตัวอย่าง (out-of-sample) ตรวจสอบค่ะ",
    ],
    "trading_platform": [
        "หากส่งคำสั่งแล้วถูก Rejected ให้ตรวจสอบ buying power/วงเงิน, ประเภทคำสั่ง (เช่น IOC/DAY), และเงื่อนไขราคา (ceiling/floor) ครับ",
        "การตั้ง Stop order/Stop limit ทำได้จากหน้าส่งคำสั่ง โดยเลือกประเภทคำสั่งเป็น Stop และกำหนด trigger price ตามต้องการค่ะ",
        "Indicator เช่น RSI/MACD เพิ่มได้จากเมนู Indicators ในหน้ากราฟ และสามารถบันทึกเป็น template ได้ครับ",
        "สามารถดู Bid/Offer และ depth (ถ้ามีสิทธิ์ข้อมูล) ได้ในหน้า order book หรือหน้ารายละเอียดหลักทรัพย์ค่ะ",
    ],
}

def generate_variations(q_text, a_text, topic_id, scenario_idx):
    """Generate multiple natural variations of a Q&A pair (Thai + English jargon mixed)."""
    base_q = _mix_jargon(q_text)
    ans = a_text

    patterns = [
        lambda: f"{_pick(OPENERS)} {base_q} {_pick(CLOSERS)}",
        lambda: f"{base_q} {_pick(['ทำยังไงดีครับ','ต้องทำอะไรบ้างครับ','มีขั้นตอนยังไงค่ะ','ขอรายละเอียดหน่อยครับ'])}",
        lambda: f"{_pick(['ถามเพิ่มครับ','ขอถามต่อค่ะ','รบกวนหน่อยครับ'])} {base_q}",
        lambda: f"{base_q} (beginner) {_pick(CLOSERS)}",
    ]
    a_patterns = [
        lambda: f"เรียนคุณลูกค้า {ans}",
        lambda: f"สวัสดีค่ะ {ans}",
        lambda: f"สวัสดีครับ {ans}",
        lambda: f"เรียนคุณลูกค้า {ans} หากต้องการให้ช่วยดูกรณีเฉพาะ กรุณาระบุสัญลักษณ์/series หรือแนบรายละเอียดเพิ่มเติมค่ะ",
    ]

    out = []
    # Ensure at least 2 variations for clustering
    for _ in range(2 + (1 if random.random() < 0.35 else 0)):
        out.append(
            {
                "customer_message": patterns[random.randrange(len(patterns))](),
                "admin_reply": a_patterns[random.randrange(len(a_patterns))](),
            }
        )
    return out

def main():
    all_data = []

    random.seed(42)
    # Increase volume + diversity: all scenarios, 2–3 variations each
    for topic_id, scenario_list in SCENARIOS.items():
        for i, scenario_text in enumerate(scenario_list):
            ans_base = _pick(ANSWER_BANK[topic_id])
            pairs = generate_variations(scenario_text, ans_base, topic_id, i)
            all_data.extend(pairs)

    # Add a small amount of realistic “follow-up” style Qs to diversify wording
    followups = [
        ("options", "IV ขึ้นแล้ว premium แพงขึ้นนี่คือ vega ใช่ไหมครับ"),
        ("futures_tfex", "ถ้าเติม margin ไม่ทัน ระบบปิดสถานะให้เลยไหม"),
        ("stocks_fundamentals", "ดูงบ CF แล้ว cash flow ติดลบควรกังวลไหมครับ"),
        ("portfolio_strategy", "ถ้าตลาดผันผวนมากควรลด position size หรือ hedge ดีครับ"),
        ("trading_platform", "ส่งคำสั่งแล้วโดน partial fill คืออะไร แปลว่ามี liquidity ไม่พอใช่ไหม"),
    ]
    for t, q in followups:
        all_data.extend(generate_variations(q, _pick(ANSWER_BANK[t]), t, 999))
            
    output_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, 'conversations.json')
    
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
        
    print(f"Generated {len(all_data)} records @ {datetime.now().isoformat(timespec='seconds')}")
    print(f"Saved to: {out_file}")

if __name__ == "__main__":
    main()
