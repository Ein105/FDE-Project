from dotenv import load_dotenv
from deepagents import create_deep_agent

load_dotenv()  # .env의 API 키를 불러옵니다


def get_exchange_rate(currency: str) -> str:
    """통화 코드(예: USD)를 받아 원화 환율을 알려줍니다."""
    rates = {"USD": 1400, "JPY": 9.2, "EUR": 1520}
    return f"1 {currency} = {rates.get(currency, '알 수 없음')}원"


agent = create_deep_agent(
    model="openai:gpt-5.5",
    tools=[get_exchange_rate],
    system_prompt="당신은 환율 계산을 돕는 비서입니다. 한국어로 답하세요.",
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "250달러는 원화로 얼마야?"}]}
)

print(result["messages"][-1].content)