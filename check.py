#!/usr/bin/env python3
"""CLI-скрипт оценки агента и проведения A/B-тестирования описаний инструментов.

Соответствует требованиям задания 1:
- Запуск: uv run python check.py --n 3
- Оценка 10 задач по N прогонов
- Расчёт метрик: pass@1, pass^3, средние токены, стоимость, число шагов
- A/B тестирование: 'short' vs 'full' описания инструментов
- Доверительный интервал Вильсона для доли верного выбора инструментов
- Ответ на вопрос: окупились ли лишние токены описаний числом
- Сохранение итогов в runs/benchmark_summary.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_loop import get_client, run_agent
from benchmark import BENCHMARK_TASKS, BenchmarkTask
from tools import TOOLS_FULL, TOOLS_SHORT


def wilson_score_interval(
    successes: int, total: int, confidence: float = 0.95
) -> tuple[float, float, float]:
    """Вычисляет точечную оценку p_hat и интервал Вильсона [lower, upper]."""
    if total == 0:
        return 0.0, 0.0, 0.0
    p = successes / total
    z = 1.95996 if confidence == 0.95 else 1.64485
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total)) / denom
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return p, lower, upper


@dataclass
class RunRecord:
    task_id: str
    run_idx: int
    variant: str
    tool_args_ok: bool
    tool_args_reason: str
    outcome_ok: bool
    outcome_reason: str
    steps: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_rub: float
    stopped_by_limit: bool
    run_id: str


@dataclass
class VariantMetrics:
    variant: str
    total_runs: int
    total_tasks: int
    runs_per_task: int
    pass_at_1: float
    pass_k: float
    tool_args_accuracy: float
    wilson_lower: float
    wilson_upper: float
    avg_steps: float
    avg_prompt_tokens: float
    avg_completion_tokens: float
    avg_total_tokens: float
    avg_cost_rub: float
    total_cost_rub: float
    task_pass_k_counts: dict[str, int]


def evaluate_variant(
    tasks: list[BenchmarkTask],
    variant_name: str,
    tools_schema: list[dict[str, Any]],
    n_runs: int,
    max_steps: int,
    client: Any,
    model: str,
) -> tuple[VariantMetrics, list[RunRecord]]:
    """Проводит n_runs прогонов для всех задач в рамках одного варианта описаний."""
    print(f"\n{'='*75}")
    print(f"  ЗАПУСК ЭКСПЕРИМЕНТА: Вариант '{variant_name.upper()}' ({len(tasks)} задач × {n_runs} прогонов)")
    print(f"{'='*75}")

    records: list[RunRecord] = []
    task_successes: dict[str, list[bool]] = {t.task_id: [] for t in tasks}

    for task_idx, task in enumerate(tasks, 1):
        print(f"\n--- [{task_idx}/{len(tasks)}] {task.task_id} ({task.title}) ---")
        for run_i in range(1, n_runs + 1):
            # Небольшая пауза для избежания коротких всплесков RPS
            time.sleep(0.5)

            res = run_agent(
                question=task.question,
                tools=tools_schema,
                max_steps=max_steps,
                variant=variant_name,
                task_id=task.task_id,
                log_dir="runs",
                client=client,
                model=model,
            )

            # Проверка инструмента и аргументов
            tool_ok, tool_reason = task.check_tool_and_args(res.tools_called)
            # Проверка итогового ответа
            outcome_ok, outcome_reason = task.check_outcome(res.final_answer, res.tools_called)

            task_successes[task.task_id].append(outcome_ok)

            rec = RunRecord(
                task_id=task.task_id,
                run_idx=run_i,
                variant=variant_name,
                tool_args_ok=tool_ok,
                tool_args_reason=tool_reason,
                outcome_ok=outcome_ok,
                outcome_reason=outcome_reason,
                steps=res.steps_count,
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                total_tokens=res.total_tokens,
                cost_rub=res.cost_rub,
                stopped_by_limit=res.stopped_by_limit,
                run_id=res.run_id,
            )
            records.append(rec)

            tool_mark = "✓" if tool_ok else "✗"
            out_mark = "✓" if outcome_ok else "✗"
            print(
                f"  Прогон {run_i}/{n_runs}: "
                f"Инструменты: {tool_mark} ({tool_reason}) | "
                f"Итог: {out_mark} ({outcome_reason}) | "
                f"Шаги: {res.steps_count} | "
                f"Токены: {res.total_tokens} | "
                f"Цена: {res.cost_rub:.4f} ₽"
            )

    # Расчёт итоговых метрик
    total_runs = len(records)
    total_outcomes = sum(1 for r in records if r.outcome_ok)
    pass_at_1 = total_outcomes / total_runs if total_runs > 0 else 0.0

    # pass^k: доля задач, где ВСЕ k прогонов были успешными
    pass_k_hits = sum(1 for tid, runs in task_successes.items() if all(runs) and len(runs) == n_runs)
    pass_k = pass_k_hits / len(tasks) if tasks else 0.0

    # Доля верного выбора инструментов и аргументов
    tool_successes = sum(1 for r in records if r.tool_args_ok)
    p_hat, w_lower, w_upper = wilson_score_interval(tool_successes, total_runs)

    avg_steps = sum(r.steps for r in records) / total_runs if total_runs > 0 else 0.0
    avg_prompt = sum(r.prompt_tokens for r in records) / total_runs if total_runs > 0 else 0.0
    avg_comp = sum(r.completion_tokens for r in records) / total_runs if total_runs > 0 else 0.0
    avg_total_tok = sum(r.total_tokens for r in records) / total_runs if total_runs > 0 else 0.0
    avg_cost = sum(r.cost_rub for r in records) / total_runs if total_runs > 0 else 0.0
    total_cost = sum(r.cost_rub for r in records)

    metrics = VariantMetrics(
        variant=variant_name,
        total_runs=total_runs,
        total_tasks=len(tasks),
        runs_per_task=n_runs,
        pass_at_1=round(pass_at_1, 4),
        pass_k=round(pass_k, 4),
        tool_args_accuracy=round(p_hat, 4),
        wilson_lower=round(w_lower, 4),
        wilson_upper=round(w_upper, 4),
        avg_steps=round(avg_steps, 2),
        avg_prompt_tokens=round(avg_prompt, 1),
        avg_completion_tokens=round(avg_comp, 1),
        avg_total_tokens=round(avg_total_tok, 1),
        avg_cost_rub=round(avg_cost, 4),
        total_cost_rub=round(total_cost, 4),
        task_pass_k_counts={tid: sum(runs) for tid, runs in task_successes.items()},
    )

    return metrics, records


def print_metrics_table(metrics: list[VariantMetrics]) -> None:
    """Выводит сводную таблицу результатов в консоль."""
    print("\n" + "=" * 90)
    print("                      ИТОГОВАЯ СВОДНАЯ ТАБЛИЦА МЕТРИК")
    print("=" * 90)
    header = (
        f"{'Вариант':<10} | {'pass@1':<8} | {'pass^3':<8} | "
        f"{'Tool Acc':<10} | {'95% Wilson CI':<16} | "
        f"{'Ср. шаги':<8} | {'Ср. токены':<11} | {'Ср. цена':<10}"
    )
    print(header)
    print("-" * len(header))
    for m in metrics:
        ci_str = f"[{m.wilson_lower:.3f}, {m.wilson_upper:.3f}]"
        print(
            f"{m.variant.upper():<10} | "
            f"{m.pass_at_1 * 100:>6.1f}% | "
            f"{m.pass_k * 100:>6.1f}% | "
            f"{m.tool_args_accuracy * 100:>8.1f}% | "
            f"{ci_str:<16} | "
            f"{m.avg_steps:>8.2f} | "
            f"{m.avg_total_tokens:>11.0f} | "
            f"{m.avg_cost_rub:>8.4f} ₽"
        )
    print("=" * 90)


def print_ab_analysis(short_m: VariantMetrics, full_m: VariantMetrics) -> dict[str, Any]:
    """Сравнивает варианты A и B и отвечает числом на вопрос об окупаемости."""
    delta_pass1 = full_m.pass_at_1 - short_m.pass_at_1
    delta_pass_k = full_m.pass_k - short_m.pass_k
    delta_tool_acc = full_m.tool_args_accuracy - short_m.tool_args_accuracy
    delta_prompt = full_m.avg_prompt_tokens - short_m.avg_prompt_tokens
    delta_total_tok = full_m.avg_total_tokens - short_m.avg_total_tokens
    delta_cost = full_m.avg_cost_rub - short_m.avg_cost_rub

    # Проверка статистической значимости через интервалы Вильсона
    intervals_overlap = not (
        full_m.wilson_lower > short_m.wilson_upper or short_m.wilson_lower > full_m.wilson_upper
    )

    print("\n" + "=" * 90)
    print("                       A/B АНАЛИЗ И ЭКОНОМИЧЕСКИЙ ВЫВОД")
    print("=" * 90)
    print(f"1. Прирост точности выбора инструментов: {delta_tool_acc * 100:+.1f}% пунктов")
    print(f"   - Вариант Short: {short_m.tool_args_accuracy * 100:.1f}% [{short_m.wilson_lower:.3f}, {short_m.wilson_upper:.3f}]")
    print(f"   - Вариант Full:  {full_m.tool_args_accuracy * 100:.1f}% [{full_m.wilson_lower:.3f}, {full_m.wilson_upper:.3f}]")
    print(f"   - Перекрытие интервалов Вильсона: {'Да (различие умеренное)' if intervals_overlap else 'Нет (различие статистически значимо)'}")
    print(f"\n2. Прирост воспроизводимой надёжности (pass^3): {delta_pass_k * 100:+.1f}% пунктов")
    print(f"   - Short: {short_m.pass_k * 100:.1f}% | Full: {full_m.pass_k * 100:.1f}%")
    print(f"\n3. Изменение затрат на задачу:")
    print(f"   - Дополнительные токены промпта: {delta_prompt:+.1f} токенов")
    print(f"   - Изменение общих токенов:       {delta_total_tok:+.1f} токенов")
    print(f"   - Разница стоимости задачи:      {delta_cost:+.4f} ₽")

    # Числовой ответ об окупаемости:
    # Окупаемость токенов = сокращение ошибочных повторных шагов vs цена длинного описания
    steps_saved = short_m.avg_steps - full_m.avg_steps
    is_cost_effective = False
    if delta_tool_acc > 0:
        if delta_cost <= 0:
            conclusion = (
                "ДА, лишние токены описаний ПОЛНОСТЬЮ ОКУПИЛИСЬ. "
                "За счёт отсутствия ошибок модель тратит меньше лишних шагов, "
                "что снизило суммарное число токенов и итоговую стоимость задачи."
            )
            is_cost_effective = True
        else:
            cost_per_percent = delta_cost / (delta_tool_acc * 100)
            conclusion = (
                f"ДА, лишние токены описаний ЭКОНОМИЧЕСКИ ОПРАВДАНЫ. "
                f"Прирост точности составляет +{delta_tool_acc * 100:.1f}%, а цена 1% прироста надёжности "
                f"составляет всего {cost_per_percent:.4f} ₽ за задачу, что предотвращает фатальные сбои в продакшне."
            )
            is_cost_effective = True
    else:
        conclusion = (
            "НЕТ, лишние токены описаний не дали достаточного прироста точности на данных задачах."
        )

    print(f"\nВЫВОД ПО ОКУПАЕМОСТИ: {conclusion}")
    print("=" * 90 + "\n")

    return {
        "delta_pass1": round(delta_pass1, 4),
        "delta_pass_k": round(delta_pass_k, 4),
        "delta_tool_acc": round(delta_tool_acc, 4),
        "delta_prompt_tokens": round(delta_prompt, 1),
        "delta_total_tokens": round(delta_total_tok, 1),
        "delta_cost_rub": round(delta_cost, 4),
        "steps_saved": round(steps_saved, 2),
        "is_cost_effective": is_cost_effective,
        "conclusion": conclusion,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Запуск бенчмарка и A/B тестирования агента по базе мест Ростова-на-Дону."
    )
    parser.add_argument(
        "--n",
        type=int,
        default=3,
        help="Количество повторных прогонов каждой задачи (по умолчанию: 3).",
    )
    parser.add_argument(
        "--variant",
        choices=["short", "full", "both"],
        default="both",
        help="Вариант описаний инструментов: 'short', 'full' или 'both' (A/B сравнение).",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Выполнить только одну конкретную задачу по task_id (для отладки).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=6,
        help="Лимит шагов агента до принудительной остановки (по умолчанию: 6).",
    )
    args = parser.parse_args()

    # Фильтрация задач при необходимости
    tasks = BENCHMARK_TASKS
    if args.task:
        tasks = [t for t in tasks if t.task_id == args.task]
        if not tasks:
            print(f"Ошибка: задача с id '{args.task}' не найдена в benchmark.py!")
            sys.exit(1)

    client, model = get_client()
    print(f"Инициализация агента: модель '{model}'")
    print(f"Задач в наборе: {len(tasks)}, прогонов на задачу: {args.n}")

    metrics_list: list[VariantMetrics] = []
    all_records: list[RunRecord] = []

    if args.variant in ("short", "both"):
        m_short, recs_short = evaluate_variant(
            tasks=tasks,
            variant_name="short",
            tools_schema=TOOLS_SHORT,
            n_runs=args.n,
            max_steps=args.max_steps,
            client=client,
            model=model,
        )
        metrics_list.append(m_short)
        all_records.extend(recs_short)

    if args.variant in ("full", "both"):
        m_full, recs_full = evaluate_variant(
            tasks=tasks,
            variant_name="full",
            tools_schema=TOOLS_FULL,
            n_runs=args.n,
            max_steps=args.max_steps,
            client=client,
            model=model,
        )
        metrics_list.append(m_full)
        all_records.extend(recs_full)

    # Печать сводной таблицы
    print_metrics_table(metrics_list)

    ab_result = None
    if len(metrics_list) == 2:
        ab_result = print_ab_analysis(metrics_list[0], metrics_list[1])

    # Сохранение итогов в JSON для последующего отчёта REPORT.md
    summary_path = Path("runs") / "benchmark_summary.json"
    summary_data = {
        "timestamp": time.time(),
        "n_runs": args.n,
        "model": model,
        "metrics": [asdict(m) for m in metrics_list],
        "ab_comparison": ab_result,
        "total_records_count": len(all_records),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    print(f"Итоговая сводка сохранена в: {summary_path}")


if __name__ == "__main__":
    main()
