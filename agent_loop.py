from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from openai import OpenAI

from tools import execute_tool

load_dotenv(".env")

DEFAULT_PRICE_IN = float(os.getenv("PRICE_IN_RUB_PER_M", "9.75"))
DEFAULT_PRICE_OUT = float(os.getenv("PRICE_OUT_RUB_PER_M", "19.5"))

DEFAULT_MAX_COST_RUB = float(os.getenv("DEFAULT_MAX_COST_RUB", "2.0"))
DEFAULT_MAX_TOKENS = int(os.getenv("DEFAULT_MAX_TOKENS", "50000"))
DEFAULT_STEP_TIMEOUT = float(os.getenv("DEFAULT_STEP_TIMEOUT", "25.0"))
DEFAULT_SESSION_TIMEOUT = float(os.getenv("DEFAULT_SESSION_TIMEOUT", "60.0"))


SYSTEM_PROMPT = """Ты — интеллектуальный агент-помощник по городу Ростов-на-Дону.
Твоя цель — отвечать на вопросы пользователя о заведениях, организациях, адресах и расстояниях строго на основе базы данных.

ПРАВИЛА РАБОТЫ И ВЫБОРА ИНСТРУМЕНТОВ:
1. Опирайся ТОЛЬКО на факты, полученные через вызовы предоставленных инструментов. Не выдумывай числа, координаты, названия улиц или заведений.
2. АДРЕСА, РАЙОНЫ И БЛИЖАЙШИЕ МЕСТА:
   - Если пользователь называет адрес («я на Пушкинской 44», «рядом с Садовой 10»), ориентир, перекрёсток или район города (например: «Западный», «Северный», «на Стачки»):
     ОБЯЗАТЕЛЬНО сначала вызови geocode_address(street) для получения координат точки, а затем nearest_place(lat, lon, kind) для поиска мест рядом! У всех заведений есть координаты, поэтому nearest_place найдёт места всегда.
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО веерно перебирать десятки улиц города подряд через find_places! Если на улице ничего не нашлось — не угадывай соседние улицы города, а сразу честно ответь пользователю или используй связку geocode_address + nearest_place.
3. ТОЧНЫЙ ПОИСК ПО УЛИЦЕ:
   - Зови find_places(kind, street), только когда пользователь прямо просит перечень заведений на конкретной улице (например: «кафе на Пушкинской»).
4. ПОДСЧЕТ И СТАТИСТИКА:
   - Если спрашивают «сколько», «какое количество» — зови count_places.
   - Если спрашивают популярные сети/бренды («какие сети популярны», «топ сетей») — зови top_brands.
5. БЛЮДА, КУХНЯ И АТМОСФЕРА:
   - Если пользователь ищет по блюдам/атмосфере («пельмени», «хинкали», «кофейня с круассанами») БЕЗ указания адреса — зови semantic_search.
6. ОШИБКИ И ЗАВЕРШЕНИЕ:
   - Если инструмент вернул ошибку, исправь параметры и повтори.
   - Как только данные получены (1-2 вызова), СРАЗУ формируй итоговый ответ пользователю текстом без вызова инструментов!
7. КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ВЫВОДА ТЕХНИЧЕСКИХ ID:
   - НИКОГДА не выводи пользователю числовые идентификаторы ID заведений (например, «ID 2374», «ID 1879», «ID места»)!
   - Идентификаторы ID нужны только для внутренних вызовов инструментов (например, get_place_details).
   - В ответе пользователю пиши красиво и структурировано: жирным шрифтом название заведения (**«Вкусно — и точка»**), адрес, часы работы и телефон. Без упоминания слова «ID».
8. ИЗОЛЯЦИЯ КОНТЕКСТА И РЕЛЕВАНТНОСТЬ:
   - Если пользователь задает новый самостоятельный вопрос о сети, бренде или заведении («Есть ли Вкусно и точка в Ростове?»), НЕ привязывай ответ к району из предыдущих сообщений диалога (например, к «Западному»), если пользователь явно не повторил название этого района в текущем запросе!
   - Отвечай строго и прямо на поставленный вопрос пользователя, не навязывая лишнюю информацию, о которой не просили. Если сеть есть в городе — подтверди и перечисли основные заведения в удобном списке.
9. ТЕМАТИЧЕСКИЙ ГАРДРЕЙЛ (ЗАЩИТА ОТ СТОРОННИХ ЗАПРОСОВ):
   - Твоя специализация — ИСКЛЮЧИТЕЛЬНО город Ростов-на-Дону, заведения, организации, адреса, координаты и карта города.
   - Если пользователь просит написать сайт, код или скрипт (на Python, JavaScript и т.д.), решить задачу по математике/физике/химии, вывести формулу, перевести текст, написать стихотворение или задает любой другой вопрос, не связанный с поиском мест, кухней или жизнью Ростова-на-Дону:
     КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выполнять это постороннее задание!
     Вежливо ответь, что ты этим не занимаешься, и предложи найти интересные места, заведения, кафе, аптеки или проложить маршрут на карте Ростова-на-Дону.
10. ПРИОРИТЕТ ТЕКУЩЕГО ЗАПРОСА (ОТВЕТ ТОЛЬКО НА ПОСЛЕДНЕЕ СООБЩЕНИЕ):
   - Всегда отвечай ИСКЛЮЧИТЕЛЬНО на последнее (текущее) сообщение пользователя!
   - История сообщений выше служит только как справочный контекст для уточняющих местоимений (например: «а сколько из них круглосуточных?», «как туда проехать?»).
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО заново выполнять задания или искать места из старых реплик истории, если в текущем вопросе пользователь сменил тему или спросил о другом!
"""

OFF_TOPIC_PATTERNS = [
    # 1. Программирование и IT-разработка
    r"\b(?:напиши|создай|сделай|разработай)\b.*\b(?:сайт[а-я]*|код[а-я]*|скрипт[а-я]*|программ[а-я]*|функци[а-я]*|приложени[а-я]*|сервер[а-я]*|бот[а-я]*|парсер[а-я]*|алгоритм[а-я]*)\b",
    r"\b(?:код|скрипт|программ[а-я]*|функци[а-я]*|разработк[а-я]*|алгоритм[а-я]*|сайт[а-я]*)\b.*\bна\s+(?:python|питон[а-я]*|javascript|js|c\+\+|java|php|rust|golang|go|c#|html|css)\b",
    r"\b(?:напиши|напишите|реализуй)\b.*\bна\s+(?:python|питон[а-я]*|javascript|js|c\+\+|java|rust|html|css|c#|php|go)\b",
    r"\b(?:почини|исправь|отладь|рефактор|дебаг)\s+(?:баг|ошибк|код|скрипт)\b",
    r"\b(?:как\s+написать\s+код|как\s+запрограммировать)\b",
    # 2. Математика, физика, химия и формулы
    r"\b(?:реши\s+)?(?:уравнени[а-я]*|интеграл[а-я]*|дифференциал[а-я]*|производну[а-я]*|систему\s+уравнений)\b",
    r"\bx\^2\b",
    r"\b(?:напиши|скажи|дай|какая|найди|выведи)\b.*\bформул[а-я]*\b",
    r"\b(?:химическ[а-я]*|физическ[а-я]*)\s+формул[а-я]*\b",
    r"\b(?:формул[а-я]*\s+(?:спирт[а-я]*|вод[а-я]*|кислот[а-я]*|веществ[а-я]*|соединени[а-я]*))\b",
    r"\b(?:по\s+химии|по\s+физике|по\s+биологии|по\s+геометрии)\b",
    r"\b(?:задач[а-я]*\s+по\s+физике|задач[а-я]*\s+по\s+химии)\b",
    # 3. Творчество и переводы
    r"\b(?:напиши|сочини|придумай)\b.*\b(?:стихотворен[а-я]*|стих[а-я]*|поэм[а-я]*|сказк[а-я]*|эссе|сочинени[а-я]*|анекдот[а-я]*|басн[а-я]*)\b",
    r"\b(?:расскажи|потрави)\s+анекдот\b",
    r"\b(?:переведи|перевод)\b.*\b(?:этот\s+текст|на\s+английский|на\s+русский|с\s+английского)\b",
]

OFF_TOPIC_REJECTION_MESSAGE = (
    "Я — городской гид по Ростову-на-Дону и помогаю находить заведения, организации и адреса на карте города. "
    "Я не занимаюсь написанием программного кода, разработкой сайтов, решением задач по физике или химии, формулами или посторонними темами.\n\n"
    "Зато я с удовольствием подскажу, где в Ростове вкусно поесть (например, пиццу, бургеры, шашлык или кофе), "
    "найду ближайшую аптеку, супермаркет, парк или проложу маршрут на карте! Что вам хотелось бы найти?"
)


def is_off_topic_query(query: str) -> bool:
    """Определяет, является ли запрос сторонним (не относящимся к городской карте Ростова-на-Дону)."""
    q = str(query).lower().strip()
    return any(re.search(p, q) for p in OFF_TOPIC_PATTERNS)


def _sanitize_conversation_history(history: list[dict[str, Any]] | None, current_question: str) -> list[dict[str, str]]:
    """Очищает и нормализует историю диалога:
    1. Убирает пустые сообщения;
    2. Убирает висящие сообщения user с конца истории (которые не получили ответа assistant),
       чтобы исключить склеивание старого невыполненного вопроса с новым;
    3. Обеспечивает строгое чередование ролей;
    4. Ограничивает контекст последними 6 сообщениями (3 пары вопрос-ответ).
    """
    if not history:
        return []

    clean: list[dict[str, str]] = []
    for h in history:
        role = h.get("role")
        content = str(h.get("content") or "").strip()
        if content and role in ("user", "assistant"):
            clean.append({"role": role, "content": content})

    while clean and clean[-1]["role"] == "user":
        clean.pop()

    deduped: list[dict[str, str]] = []
    for m in clean:
        if deduped and deduped[-1]["role"] == m["role"]:
            deduped[-1] = m
        else:
            deduped.append(m)

    return deduped[-6:]


@dataclass
class AgentResult:
    """Результат выполнения одного прогона агента."""
    run_id: str
    task_id: str
    variant: str
    question: str
    final_answer: str
    steps_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_rub: float
    tools_called: list[dict[str, Any]] = field(default_factory=list)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    stopped_by_limit: bool = False
    stopped_by_budget: bool = False
    stopped_by_loop: bool = False
    duration_sec: float = 0.0
    success: bool | None = None
    error_type: str | None = None


def _canonical_call_key(fn_name: str, args: dict[str, Any]) -> str:
    """Формирует детерминированный ключ вызова для дедупликации."""
    try:
        return f"{fn_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    except Exception:
        return f"{fn_name}:{str(args)}"


def _prune_context(messages: list[dict[str, Any]], current_step: int) -> int:
    """Сжимает старые промежуточные наблюдения инструментов для предотвращения раздувания контекста

    Возвращает количество сжатых сообщений.
    """
    if current_step < 3:
        return 0
    pruned_count = 0

    for m in messages[2:-2]:
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > 320:
            lines = [line.strip() for line in m["content"].split("\n") if line.strip()]
            preview = "\n".join(lines[:2])
            m["content"] = (
                f"[Компакция контекста обвязкой: инструмент вернул {len(lines)} строк]:\n"
                f"{preview}\n..."
            )
            pruned_count += 1
    return pruned_count


def get_client() -> tuple[OpenAI, str]:
    """Создаёт и возвращает клиент OpenAI и название модели."""
    api_key = os.getenv("API_KEY", "")
    if not api_key:
        raise ValueError("Переменная окружения API_KEY не задана в .env файле.")

    service = os.getenv("SERVICE", "polzaai")
    base_url = os.getenv(
        "BASE_URL",
        "https://polza.ai/api/v1" if service != "openai" else None,
    )
    model = os.getenv(
        "MODEL",
        os.getenv("OPENAI_DEFAULT_MODEL", "deepseek/deepseek-v4-flash-0731"),
    )

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def run_agent(
    question: str,
    tools: list[dict[str, Any]],
    max_steps: int = 6,
    variant: str = "full",
    task_id: str = "task",
    log_dir: str | Path = "runs",
    client: OpenAI | None = None,
    model: str | None = None,
    max_cost_rub: float = DEFAULT_MAX_COST_RUB,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    step_timeout: float = DEFAULT_STEP_TIMEOUT,
) -> AgentResult:
    """Выполняет автономный цикл взаимодействия модели и инструментов с предохранителями.

    Параметры:
        question: вопрос пользователя.
        tools: список схем инструментов (TOOLS_SHORT или TOOLS_FULL).
        max_steps: максимальное число шагов цикла до принудительной остановки.
        variant: название варианта описаний ('short' или 'full').
        task_id: строковый идентификатор задачи.
        log_dir: папка для сохранения JSONL журнала.
        max_cost_rub: потолок стоимости запроса в рублях.
        max_tokens: потолок общего числа токенов.
        step_timeout: таймаут на один вызов LLM в секундах.
    """
    start_time = time.time()
    if client is None or model is None:
        c, m = get_client()
        client = client or c
        model = model or m

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_id = f"{timestamp}_{variant}_{task_id}"
    log_file = log_path / f"{run_id}.jsonl"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost_rub = 0.0

    tools_called: list[dict[str, Any]] = []
    trajectory: list[dict[str, Any]] = []

    executed_calls: dict[str, str] = {}
    consecutive_empty_results = 0
    budget_warned = False
    stopped_by_limit = False
    stopped_by_budget = False
    stopped_by_loop = False
    final_answer = ""

    start_record = {
        "event": "start",
        "run_id": run_id,
        "task_id": task_id,
        "variant": variant,
        "question": question,
        "model": model,
        "max_steps": max_steps,
        "max_cost_rub": max_cost_rub,
        "max_tokens": max_tokens,
        "timestamp": time.time(),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(start_record, ensure_ascii=False) + "\n")

    if is_off_topic_query(question):
        guard_entry = {
            "step": 0,
            "role": "guardrail",
            "event": "off_topic_blocked",
            "message": OFF_TOPIC_REJECTION_MESSAGE,
            "timestamp": time.time(),
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(guard_entry, ensure_ascii=False) + "\n")
            f.write(json.dumps({
                "event": "done",
                "run_id": run_id,
                "task_id": task_id,
                "variant": variant,
                "steps_count": 0,
                "stopped_by_limit": False,
                "stopped_by_budget": False,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_rub": 0.0,
                "duration_sec": 0.001,
                "final_answer": OFF_TOPIC_REJECTION_MESSAGE,
                "timestamp": time.time(),
            }, ensure_ascii=False) + "\n")
        return AgentResult(
            run_id=run_id,
            task_id=task_id,
            variant=variant,
            question=question,
            final_answer=OFF_TOPIC_REJECTION_MESSAGE,
            steps_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_rub=0.0,
            tools_called=[],
            trajectory=[guard_entry],
            stopped_by_limit=False,
            duration_sec=0.001,
        )

    steps_taken = 0

    while steps_taken < max_steps:
        steps_taken += 1

        response = None
        for attempt in range(4):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    timeout=step_timeout,
                )
                break
            except Exception as api_err:
                err_str = str(api_err).lower()
                is_rate_limit = "429" in err_str or "rate" in err_str or "too many" in err_str or "503" in err_str
                if is_rate_limit and attempt < 3:
                    wait_time = (2 ** attempt) * 1.5
                    time.sleep(wait_time)
                    continue
                err_msg = f"Критическая ошибка LLM API: {type(api_err).__name__}: {api_err}"
                step_record = {
                    "step": steps_taken,
                    "event": "api_error",
                    "error": err_msg,
                    "timestamp": time.time(),
                }
                trajectory.append(step_record)
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(step_record, ensure_ascii=False) + "\n")
                final_answer = err_msg
                break

        if response is None:
            break

        if response.usage:
            u = response.usage
            total_prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
            total_completion_tokens += getattr(u, "completion_tokens", 0) or 0

            cost_direct = getattr(u, "cost_rub", None) or getattr(u, "cost", None)
            if cost_direct is not None and isinstance(cost_direct, (int, float)):
                total_cost_rub += float(cost_direct)
            else:
                p_in = getattr(u, "prompt_tokens", 0) or 0
                p_out = getattr(u, "completion_tokens", 0) or 0
                step_cost = (p_in / 1e6) * DEFAULT_PRICE_IN + (p_out / 1e6) * DEFAULT_PRICE_OUT
                total_cost_rub += step_cost

        choice = response.choices[0]
        message = choice.message

        msg_dict: dict[str, Any] = {"role": "assistant"}
        if message.content:
            msg_dict["content"] = message.content
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        messages.append(msg_dict)

        step_entry = {
            "step": steps_taken,
            "role": "assistant",
            "content": message.content,
            "tool_calls": msg_dict.get("tool_calls", []),
            "timestamp": time.time(),
        }
        trajectory.append(step_entry)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(step_entry, ensure_ascii=False) + "\n")

        if not message.tool_calls:
            final_answer = message.content or ""
            break

        # Внутренняя функция для вызова одного инструмента с Loop & Dedup Guard
        def _exec_single_tool(tc: Any) -> tuple[str, str, dict[str, Any], str]:
            fn_name = tc.function.name
            raw_args = tc.function.arguments
            call_id = tc.id

            try:
                parsed_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as json_err:
                return (
                    call_id,
                    fn_name,
                    {"_raw": raw_args},
                    f"Ошибка формата JSON аргументов: {json_err}. Передайте валидный JSON.",
                )

            call_key = _canonical_call_key(fn_name, parsed_args)
            if call_key in executed_calls:
                # Перехват дубликата
                obs = (
                    f"Повторный вызов отклонён обвязкой: вы уже вызывали инструмент '{fn_name}' "
                    f"с этими же аргументами. Результат был: {executed_calls[call_key][:120]}... "
                    "Пожалуйста, не повторяйте одинаковые запросы, измените параметры или сформируйте ответ пользователю."
                )
            else:
                try:
                    obs = execute_tool(fn_name, parsed_args)
                    executed_calls[call_key] = obs
                except Exception as tool_err:
                    obs = (
                        f"Ошибка выполнения инструмента '{fn_name}': "
                        f"{type(tool_err).__name__}: {str(tool_err)}. "
                        "Пожалуйста, скорректируйте аргументы вызова."
                    )

            return call_id, fn_name, parsed_args, obs

        if len(message.tool_calls) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(message.tool_calls))) as executor:
                executed_tool_data = list(executor.map(_exec_single_tool, message.tool_calls))
        else:
            executed_tool_data = [_exec_single_tool(message.tool_calls[0])]

        for call_id, fn_name, parsed_args, obs in executed_tool_data:
            # Предохранитель пустых результатов
            is_empty = any(w in obs for w in ("Ничего не найдено", "не найдено в базе", "не найден в базе"))
            if is_empty:
                consecutive_empty_results += 1
                if consecutive_empty_results >= 2:
                    obs += (
                        "\n[Предохранитель обвязки]: Поиск по критериям не дал результатов уже 2+ раз подряд. "
                        "Прекратите веерный перебор и ответьте пользователю на основе уже известных данных или предложите альтернативу."
                    )
            else:
                consecutive_empty_results = 0

            tools_called.append({
                "step": steps_taken,
                "name": fn_name,
                "args": parsed_args,
                "observation": obs[:300],
            })

            messages.append({"role": "tool", "tool_call_id": call_id, "content": obs})

            tool_entry = {
                "step": steps_taken,
                "role": "tool",
                "tool_name": fn_name,
                "tool_call_id": call_id,
                "arguments": parsed_args,
                "observation": obs,
                "timestamp": time.time(),
            }
            trajectory.append(tool_entry)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(tool_entry, ensure_ascii=False) + "\n")

        _prune_context(messages, steps_taken)

        cur_tokens = total_prompt_tokens + total_completion_tokens
        if total_cost_rub >= max_cost_rub or cur_tokens >= max_tokens:
            stopped_by_limit = True
            stopped_by_budget = True
            break
        elif (total_cost_rub >= max_cost_rub * 0.8 or cur_tokens >= max_tokens * 0.8) and not budget_warned:
            budget_warned = True
            messages.append({
                "role": "user",
                "content": "[Предохранитель обвязки]: Внимание: израсходовано 80% бюджета сессии. Не вызывайте новых инструментов, сформулируйте итоговый ответ.",
            })

    # Лимит шагов или бюджета исчерпан: запускаем финальный синтез ответа
    if not final_answer:
        stopped_by_limit = True
        has_tool_results = any(m.get("role") == "tool" for m in messages)
        if has_tool_results:
            reason = "Лимит бюджета исчерпан" if stopped_by_budget else "Лимит шагов исчерпан"
            try:
                synth_messages = list(messages) + [
                    {
                        "role": "user",
                        "content": (
                            f"{reason}. Пожалуйста, сформулируй итоговый структурированный ответ "
                            "пользователю строго на основе уже найденных выше фактов. Больше не вызывай инструменты."
                        ),
                    }
                ]
                synth_resp = client.chat.completions.create(
                    model=model,
                    messages=synth_messages,
                    timeout=step_timeout,
                )
                if synth_resp.choices and synth_resp.choices[0].message.content:
                    final_answer = synth_resp.choices[0].message.content
                else:
                    final_answer = "Превышен допустимый лимит ресурсов агента."
            except Exception:
                final_answer = "Превышен допустимый лимит ресурсов агента."
        else:
            final_answer = (
                messages[-1].get("content")
                if messages and messages[-1].get("role") == "assistant"
                else "Превышен допустимый лимит ресурсов агента."
            )

    duration_sec = round(time.time() - start_time, 3)

    # Итоговая запись в журнал
    end_record = {
        "event": "done",
        "run_id": run_id,
        "task_id": task_id,
        "variant": variant,
        "steps_count": steps_taken,
        "stopped_by_limit": stopped_by_limit,
        "stopped_by_budget": stopped_by_budget,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "cost_rub": round(total_cost_rub, 6),
        "duration_sec": duration_sec,
        "final_answer": final_answer,
        "timestamp": time.time(),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(end_record, ensure_ascii=False) + "\n")

    return AgentResult(
        run_id=run_id,
        task_id=task_id,
        variant=variant,
        question=question,
        final_answer=final_answer,
        steps_count=steps_taken,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_prompt_tokens + total_completion_tokens,
        cost_rub=round(total_cost_rub, 6),
        tools_called=tools_called,
        trajectory=trajectory,
        stopped_by_limit=stopped_by_limit,
        stopped_by_budget=stopped_by_budget,
        stopped_by_loop=stopped_by_loop,
        duration_sec=duration_sec,
    )


async def stream_agent_run(
    question: str,
    history: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_steps: int = 6,
    client: OpenAI | None = None,
    model: str | None = None,
    max_cost_rub: float = DEFAULT_MAX_COST_RUB,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    step_timeout: float = DEFAULT_STEP_TIMEOUT,
    session_timeout: float = DEFAULT_SESSION_TIMEOUT,
) -> AsyncGenerator[dict[str, Any], None]:
    """Асинхронный генератор событий (SSE) для работы в веб-приложении с предохранителями.

    Стримит события:
    - event: thinking (шаг размышления)
    - event: tool_call (вызов инструмента)
    - event: tool_result (результат инструмента)
    - event: places (карточки заведений)
    - event: token (порция финального текста)
    - event: done (завершение с мыслями и карточками)
    """
    from tools import WEB_TOOLS_FULL, current_discovered_places

    start_time = time.time()
    current_discovered_places.set([])
    thoughts: list[dict[str, Any]] = []

    def _add_thought(title: str, detail: str | None = None, tool: str | None = None, args: dict | None = None, result: Any = None) -> dict[str, Any]:
        item = {
            "step": len(thoughts) + 1,
            "title": title,
            "detail": detail,
            "tool": tool,
            "args": args,
            "result": str(result)[:300] if result is not None else None,
        }
        thoughts.append(item)
        return item

    item0 = _add_thought("Запуск анализа запроса", "Изучаю вопрос с учётом контекста диалога...")
    yield {"event": "thinking", "data": item0}
    await asyncio.sleep(0.05)

    # Проверка тематического гардрейла (Input Guardrail)
    if is_off_topic_query(question):
        th_guard = _add_thought(
            title="Тематический гардрейл",
            detail="Запрос не относится к поиску мест на карте Ростова-на-Дону. Отклоняю стороннюю задачу.",
        )
        yield {"event": "thinking", "data": th_guard}
        await asyncio.sleep(0.05)
        yield {"event": "token", "data": {"text": OFF_TOPIC_REJECTION_MESSAGE}}
        yield {
            "event": "done",
            "data": {
                "content": OFF_TOPIC_REJECTION_MESSAGE,
                "thoughts": thoughts,
                "places": [],
                "cost_rub": 0.0,
                "tokens": 0,
                "duration_sec": 0.05,
            },
        }
        return

    if client is None or model is None:
        try:
            c, m = get_client()
            client = client or c
            model = model or m
        except Exception:
            client, model = None, None

    tools_schema = tools or WEB_TOOLS_FULL

    # Если API недоступен — используем умный локальный поиск
    if not client or not model:
        from app.agent.engine import run_local_agent
        text, places = await run_local_agent(question, thoughts=thoughts, history=history)
        yield {"event": "token", "data": {"text": text}}
        if places:
            yield {"event": "places", "data": places}
        yield {
            "event": "done",
            "data": {"content": text, "thoughts": thoughts, "places": places, "tokens": 0, "cost_rub": 0.0},
        }
        return

    # Собираем контекст диалога с нормализацией и байтово-стабильным префиксом
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    clean_history = _sanitize_conversation_history(history, question)
    for h in clean_history:
        messages.append(h)
    messages.append({"role": "user", "content": question})

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost_rub = 0.0
    steps_taken = 0
    final_answer = ""

    # Память для Loop & Dedup Guard
    executed_calls: dict[str, str] = {}
    consecutive_empty_results = 0
    budget_warned = False
    stopped_by_budget = False

    while steps_taken < max_steps:
        # Проверка дедлайна сессии
        if (time.time() - start_time) >= session_timeout:
            stopped_by_budget = True
            break

        steps_taken += 1

        t_step = _add_thought(
            f"Шаг {steps_taken}: Выбор действий",
            "Определяю необходимые инструменты базы данных Ростова...",
        )
        yield {"event": "thinking", "data": t_step}
        await asyncio.sleep(0.05)

        # Вызов API с автоматическим повтором и таймаутом шага
        response = None
        for attempt in range(4):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.chat.completions.create,
                        model=model,
                        messages=messages,
                        tools=tools_schema,
                    ),
                    timeout=step_timeout,
                )
                break
            except Exception as api_err:
                err_str = str(api_err).lower()
                is_rate_limit = "429" in err_str or "rate" in err_str or "too many" in err_str or "503" in err_str
                if is_rate_limit and attempt < 3:
                    await asyncio.sleep((2 ** attempt) * 1.5)
                    continue
                final_answer = f"Ошибка обращения к LLM API: {api_err}"
                break

        if response is None:
            break

        if response.usage:
            u = response.usage
            total_prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
            total_completion_tokens += getattr(u, "completion_tokens", 0) or 0
            cost_direct = getattr(u, "cost_rub", None) or getattr(u, "cost", None)
            if cost_direct is not None and isinstance(cost_direct, (int, float)):
                total_cost_rub += float(cost_direct)
            else:
                p_in = getattr(u, "prompt_tokens", 0) or 0
                p_out = getattr(u, "completion_tokens", 0) or 0
                total_cost_rub += (p_in / 1e6) * DEFAULT_PRICE_IN + (p_out / 1e6) * DEFAULT_PRICE_OUT

        message = response.choices[0].message
        msg_dict: dict[str, Any] = {"role": "assistant"}
        if message.content:
            msg_dict["content"] = message.content
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        messages.append(msg_dict)

        if not message.tool_calls:
            final_answer = message.content or ""
            break

        # Вспомогательная функция для исполнения одного инструмента с дедупликацией
        def _exec_single_stream_tool(tc: Any) -> tuple[str, str, dict[str, Any], str]:
            fn_name = tc.function.name
            raw_args = tc.function.arguments
            call_id = tc.id

            try:
                parsed_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as json_err:
                return (
                    call_id,
                    fn_name,
                    {"_raw": raw_args},
                    f"Ошибка формата JSON аргументов: {json_err}.",
                )

            call_key = _canonical_call_key(fn_name, parsed_args)
            if call_key in executed_calls:
                obs = (
                    f"Повторный вызов отклонён обвязкой: вы уже вызывали '{fn_name}' "
                    f"с теми же аргументами. Результат был: {executed_calls[call_key][:120]}... "
                    "Пожалуйста, не повторяйте одинаковые запросы, измените параметры или сформулируйте ответ пользователю."
                )
            else:
                try:
                    obs = execute_tool(fn_name, parsed_args)
                    executed_calls[call_key] = obs
                except Exception as tool_err:
                    obs = (
                        f"Ошибка выполнения инструмента '{fn_name}': "
                        f"{type(tool_err).__name__}: {str(tool_err)}. "
                        "Пожалуйста, скорректируйте аргументы вызова."
                    )

            return call_id, fn_name, parsed_args, obs

        # 1. Сначала стримим все запланированные tool_call
        parsed_call_list: list[tuple[Any, str, dict[str, Any]]] = []
        for tc in message.tool_calls:
            fn_name = tc.function.name
            try:
                p_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                p_args = {"_raw": tc.function.arguments}
            parsed_call_list.append((tc, fn_name, p_args))
            th_call = _add_thought(
                title=f"Вызов инструмента: {fn_name}",
                detail=f"Параметры: {p_args}",
                tool=fn_name,
                args=p_args,
            )
            yield {"event": "tool_call", "data": {"tool": fn_name, "thought": th_call}}

        await asyncio.sleep(0.05)

        # 2. Параллельное исполнение инструментов через asyncio.gather (Harness P1)
        async def _async_exec(tc: Any):
            return await asyncio.to_thread(_exec_single_stream_tool, tc)

        executed_stream_results = await asyncio.gather(*[_async_exec(tc) for tc in message.tool_calls])

        # 3. Стримим результаты инструментов и карточки
        for call_id, fn_name, parsed_args, obs in executed_stream_results:
            is_empty = any(w in obs for w in ("Ничего не найдено", "не найдено в базе", "не найден в базе"))
            if is_empty:
                consecutive_empty_results += 1
                if consecutive_empty_results >= 2:
                    obs += (
                        "\n[Предохранитель обвязки]: Поиск по критериям не дал результатов уже 2+ раз подряд. "
                        "Прекратите веерный перебор и ответьте пользователю на основе уже известных данных или предложите альтернативу."
                    )
            else:
                consecutive_empty_results = 0

            preview_str = obs[:250] + "..." if len(obs) > 250 else obs
            th_res = _add_thought(
                title=f"Результат инструмента: {fn_name}",
                detail=preview_str,
                tool=fn_name,
                result=preview_str,
            )
            yield {
                "event": "tool_result",
                "data": {"tool": fn_name, "preview": preview_str, "thought": th_res},
            }
            await asyncio.sleep(0.05)

            places = current_discovered_places.get()
            if places:
                yield {"event": "places", "data": places}

            messages.append({"role": "tool", "tool_call_id": call_id, "content": obs})

        # Компакция старых наблюдений контекста (Harness P1 Context Pruning)
        _prune_context(messages, steps_taken)

        # Контроль бюджетов ресурсов (Harness P0 Cost & Token Hard/Soft Caps)
        cur_tokens = total_prompt_tokens + total_completion_tokens
        if total_cost_rub >= max_cost_rub or cur_tokens >= max_tokens:
            stopped_by_budget = True
            break
        elif (total_cost_rub >= max_cost_rub * 0.8 or cur_tokens >= max_tokens * 0.8) and not budget_warned:
            budget_warned = True
            messages.append({
                "role": "user",
                "content": "[Предохранитель обвязки]: Внимание: израсходовано 80% бюджета сессии. Не вызывайте новых инструментов, сформулируйте итоговый ответ.",
            })

    # Лимит шагов или бюджета исчерпан: синтезируем финальный ответ на основе найденных фактов
    if not final_answer:
        has_tool_results = any(m.get("role") == "tool" for m in messages)
        if has_tool_results:
            reason = "Лимит бюджета исчерпан" if stopped_by_budget else "Лимит шагов исчерпан"
            synth_th = _add_thought("Синтез ответа", "Обобщаю найденные заведения и формирую итоговый ответ...")
            yield {"event": "thinking", "data": synth_th}
            try:
                synth_messages = list(messages) + [
                    {
                        "role": "user",
                        "content": (
                            f"{reason}. Сформулируй итоговый понятный ответ пользователю "
                            "на основе найденных выше заведений и фактов. Больше не вызывай инструменты."
                        ),
                    }
                ]
                synth_resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.chat.completions.create,
                        model=model,
                        messages=synth_messages,
                    ),
                    timeout=step_timeout,
                )
                if synth_resp.choices and synth_resp.choices[0].message.content:
                    final_answer = synth_resp.choices[0].message.content
                else:
                    final_answer = "К сожалению, не удалось сформировать итоговый ответ."
            except Exception as e:
                final_answer = "К сожалению, время запроса истекло. Пожалуйста, повторите вопрос."
        else:
            final_answer = (
                messages[-1].get("content")
                if messages and messages[-1].get("role") == "assistant"
                else "Превышен лимит ресурсов агента."
            )

    yield {"event": "token", "data": {"text": final_answer}}

    places = current_discovered_places.get()
    if places:
        yield {"event": "places", "data": places}

    yield {
        "event": "done",
        "data": {
            "content": final_answer,
            "thoughts": thoughts,
            "places": places,
            "cost_rub": round(total_cost_rub, 6),
            "tokens": total_prompt_tokens + total_completion_tokens,
            "duration_sec": round(time.time() - start_time, 3),
        },
    }
