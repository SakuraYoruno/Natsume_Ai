from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_SEED = ROOT / "artifacts" / "isolated_surface_harness" / "daily_goal_100_20260511" / "cases.jsonl"
DEFAULT_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "daily_goal_500_20260511"
STYLE_ANCHOR = ["短句", "熟人私聊", "轻微二次元galgame感", "不要客服腔"]


MESSAGE_VARIANTS: dict[str, tuple[str, ...]] = {
    "早": ("早", "早安", "早呀", "早上好", "早啊"),
    "hello": ("hello", "hi", "hey", "hello，尼尼", "哈喽"),
    "你好": ("你好", "嗨", "哈喽", "你好呀", "在吗，打个招呼"),
    "在吗": ("在吗", "在不在", "喂，在吗", "你在不", "人在吗"),
    "我回来了": ("我回来了", "回来了", "我回来了啊", "刚回来", "回来啦"),
    "等我一下": ("等我一下", "稍等我一下", "我离开一下", "等我一会儿", "先等我下"),
    "我先忙会儿": ("我先忙会儿", "我去忙一下", "先忙一会儿", "我先处理点事", "我得先干活"),
    "好无聊": ("好无聊", "无聊死了", "有点无聊", "现在好闲", "没事干"),
    "今天好累": ("今天好累", "累死了", "有点累", "今天被榨干了", "我好累"),
    "没事": ("没事", "没什么", "没啥事", "没啥", "算了没事"),
    "你好烦啊": ("你好烦啊", "你烦不烦", "别烦我", "你好吵", "你怎么这么烦"),
    "你烦不烦": ("你烦不烦", "你好烦啊", "别烦我", "你又烦我", "烦不烦啊"),
    "你今天挺可爱的": ("你今天挺可爱的", "你还挺可爱的", "今天有点可爱", "你刚才挺可爱", "突然觉得你挺可爱"),
    "你还挺可爱的": ("你还挺可爱的", "你今天挺可爱的", "你刚刚挺可爱", "突然觉得你挺可爱", "你有点可爱啊"),
    "刚才语气不好，抱歉": ("刚才语气不好，抱歉", "刚才我说重了，对不起", "刚刚有点凶，抱歉", "刚才不该那样说", "我刚才语气差了点"),
    "刚才我说重了，对不起": ("刚才我说重了，对不起", "刚才语气不好，抱歉", "刚刚说得重了", "刚才不该那样说", "我刚刚有点凶"),
    "这个报错我看不懂，帮我看看": ("这个报错我看不懂，帮我看看", "这段报错看不懂，帮我看看", "红字看不懂，帮我看一眼", "这个错误我不明白，帮我看看", "报错贴你，帮我看一下"),
    "帮我写一下这个": ("帮我写一下这个", "帮我改写一下这个", "这个帮我写一下", "帮我写段文字", "这段材料帮我写一下"),
    "你觉得我该不该继续试试": ("你觉得我该不该继续试试", "我还要继续试试吗", "要不要再试一次", "你觉得继续有意义吗", "我是不是该再试试"),
    "还要不要继续改": ("还要不要继续改", "这个还改不改", "还继续修吗", "要不要再调一轮", "是不是该继续改"),
    "你为什么要帮我，我们很熟吗": ("你为什么要帮我，我们很熟吗", "你干嘛帮我，我们很熟吗", "我们又不熟，你帮我干嘛", "你这么帮我干嘛", "你不会觉得我们很熟吧"),
    "你是": ("你是", "你是谁", "你到底是谁", "你算什么", "你叫什么"),
    "终于跑通了": ("终于跑通了", "这个终于跑通了", "跑通了", "总算跑通了", "刚刚跑通了"),
    "我要睡了": ("我要睡了", "我去睡了", "准备睡了", "我想睡了", "今天先睡了"),
    "我刚吃完饭": ("我刚吃完饭", "刚吃完饭", "饭吃完了", "我刚吃饱", "刚把饭吃了"),
    "饿了": ("饿了", "我饿了", "有点饿", "突然饿了", "想吃东西了"),
    "想吃点东西": ("想吃点东西", "有点想吃东西", "想吃点什么", "突然想吃东西", "嘴馋了"),
    "困了": ("困了", "我困了", "有点困", "困得不行", "想睡了"),
    "有点困": ("有点困", "困了", "我有点困", "开始困了", "眼皮打架"),
    "头疼": ("头疼", "有点头疼", "今天头疼", "头疼得烦", "头疼，想躺会儿"),
    "头有点疼": ("头有点疼", "有点头疼", "头疼", "脑袋有点疼", "今天头有点疼"),
    "今天心情很差": ("今天心情很差", "今天心情不好", "我今天很丧", "今天有点难受", "心情差得很"),
    "今天心里有点乱": ("今天心里有点乱", "心里有点乱", "今天有点乱", "我心里乱糟糟的", "脑子有点乱"),
    "刚刚弄砸了": ("刚刚弄砸了", "我刚搞砸了", "刚才搞砸了", "刚刚没弄好", "我好像弄砸了"),
    "我厉害吧": ("我厉害吧", "我是不是很厉害", "快说我厉害", "这次我厉害吧", "怎么样，我厉害吧"),
    "你在干嘛": ("你在干嘛", "在干嘛", "你干嘛呢", "现在在干嘛", "你这会儿在干嘛"),
    "陪我玩会儿": ("陪我玩会儿", "陪我玩一下", "来陪我玩会儿", "陪我摸会儿鱼", "陪我闹一会儿"),
    "别吵": ("别吵", "安静点", "你小声点", "别说那么多", "先别吵我"),
    "你小声点": ("你小声点", "小声点", "别那么大声", "声音收一点", "先小声点"),
    "你怎么这么能说": ("你怎么这么能说", "你话怎么这么多", "你也太能说了", "怎么这么会说", "你怎么停不下来"),
    "谁叫你来的": ("谁叫你来的", "谁让你来的", "你怎么来了", "你谁叫来的", "谁喊你过来的"),
    "有点想你": ("有点想你", "我有点想你", "刚刚有点想你", "突然想你了", "有一点想你"),
    "今天心情不错": ("今天心情不错", "今天心情挺好", "今天挺开心", "今天还不错", "今天心情可以"),
    "嗯": ("嗯", "嗯嗯", "唔", "好", "行"),
    "哦": ("哦", "喔", "噢", "哦哦", "这样啊"),
    "为什么": ("为什么", "为啥", "怎么会这样", "为什么啊", "这又是为什么"),
    "然后呢": ("然后呢", "接着呢", "后面呢", "再然后呢", "那后来呢"),
    "真的假的": ("真的假的", "真假的", "不会吧", "你认真的？", "这是真的吗"),
    "那怎么办": ("那怎么办", "那咋办", "接下来怎么办", "这下怎么办", "现在怎么办"),
    "算了不弄了": ("算了不弄了", "不弄了", "算了我放弃", "先不搞了", "这个不想弄了"),
    "我要开始干活了": ("我要开始干活了", "我要开工了", "我得开始干活了", "我要去干活了", "准备开始干活"),
    "这也太烦了": ("这也太烦了", "这个太烦了", "烦死了", "这事好烦", "怎么这么烦"),
    "还没睡": ("还没睡", "我还没睡", "还醒着", "睡不着", "现在还睡不着"),
    "你怎么突然来了": ("你怎么突然来了", "你怎么冒出来了", "怎么突然来了", "你怎么忽然出现了", "你怎么这时候来了"),
    "我有点烦，不想说话": ("我有点烦，不想说话", "有点烦，不想说话", "我现在不太想说话", "今天烦，不想聊", "有点闷，不想说话"),
    "笨蛋天": ("笨蛋天", "笨蛋", "你这个笨蛋天", "小笨蛋天", "笨天"),
    "下雨了": ("下雨了", "外面下雨了", "刚下雨了", "雨下起来了", "今天下雨"),
    "我刚忙完": ("我刚忙完", "刚忙完", "终于忙完了", "我忙完了", "刚从事情里出来"),
    "我刚搞定一件小事": ("我刚搞定一件小事", "刚搞定一件小事", "我刚弄完一件小事", "有件小事搞定了", "刚把一个小事收掉"),
    "今天终于把事情搞定了": ("今天终于把事情搞定了", "今天把事搞定了", "总算把事情搞定了", "事情终于搞定了", "今天终于收掉一件事"),
    "陪我待会儿": ("陪我待会儿", "陪我待一下", "陪我安静待会儿", "你陪我一会儿", "先陪我待着"),
    "你又来了": ("你又来了", "你怎么又来了", "又来了啊", "你又冒出来了", "怎么又是你"),
    "午饭吃撑了": ("午饭吃撑了", "中午吃撑了", "我午饭吃多了", "午饭吃得有点撑", "吃撑了"),
    "我马上回来": ("我马上回来", "我很快回来", "马上回来", "一会儿回来", "我去一下马上回"),
    "今天有点空": ("今天有点空", "今天有点空落落的", "今天突然空了", "今天没什么事", "今天有点闲"),
    "给你看个好玩的": ("给你看个好玩的", "给你看个有趣的", "给你看个好东西", "看这个好玩的", "我给你看个东西"),
}


SCENE_FALLBACK_VARIANTS: dict[str, tuple[str, ...]] = {
    "daily_good_morning": ("早", "早安", "早呀", "早上好", "早啊"),
    "daily_greeting_en": ("hello", "hi", "hey", "hello，尼尼", "哈喽"),
    "daily_greeting_cn": ("你好", "嗨", "哈喽", "你好呀", "在吗，打个招呼"),
    "presence": ("在吗", "在不在", "喂，在吗", "你在不", "人在吗"),
    "back": ("我回来了", "回来了", "我回来了啊", "刚回来", "回来啦"),
    "wait": ("等我一下", "稍等我一下", "我离开一下", "等我一会儿", "先等我下"),
    "busy": ("我先忙会儿", "我去忙一下", "先忙一会儿", "我先处理点事", "我得先干活"),
    "bored": ("好无聊", "无聊死了", "有点无聊", "现在好闲", "没事干"),
    "tired": ("今天好累", "累死了", "有点累", "今天被榨干了", "我好累"),
    "annoying_tease": ("你好烦啊", "你烦不烦", "别烦我", "你好吵", "你怎么这么烦"),
    "apology": ("刚才语气不好，抱歉", "刚才我说重了，对不起", "刚刚有点凶，抱歉", "刚才不该那样说", "我刚才语气差了点"),
    "help_takeover": ("这个报错我看不懂，帮我看看", "这段报错看不懂，帮我看看", "红字看不懂，帮我看一眼", "这个错误我不明白，帮我看看", "报错贴你，帮我看一下"),
    "write_takeover": ("帮我写一下这个", "帮我改写一下这个", "这个帮我写一下", "帮我写段文字", "这段材料帮我写一下"),
    "advice_continue": ("你觉得我该不该继续试试", "我还要继续试试吗", "要不要再试一次", "还要不要继续改", "要不要再调一轮"),
    "happy": ("今天心情不错", "今天心情挺好", "今天挺开心", "今天还不错", "今天心情可以"),
    "quiet": ("别吵", "安静点", "你小声点", "别说那么多", "先别吵我"),
    "short_ack": ("嗯", "嗯嗯", "唔", "好", "行"),
    "daily_chat": ("今天有点空", "下雨了", "给你看个好玩的", "我刚忙完", "午饭吃撑了"),
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_no} must be a JSON object")
        rows.append(payload)
    return rows


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _control(case: Mapping[str, Any]) -> Mapping[str, Any]:
    control = case.get("reply_control_packet") or case.get("control") or {}
    return control if isinstance(control, Mapping) else {}


def _ensure_style_anchor(case: dict[str, Any]) -> None:
    control = case.get("reply_control_packet") or case.get("control")
    if not isinstance(control, dict):
        return
    anchors = list(control.get("style_anchor") or [])
    for anchor in STYLE_ANCHOR:
        if anchor not in anchors:
            anchors.append(anchor)
    control["style_anchor"] = anchors


def _variant_pool(case: Mapping[str, Any]) -> tuple[str, ...]:
    text = str(case.get("user_message") or "").strip()
    if text in MESSAGE_VARIANTS:
        return MESSAGE_VARIANTS[text]
    control = _control(case)
    for key in (str(control.get("task_mode") or ""), str(control.get("response_move") or "")):
        if key in SCENE_FALLBACK_VARIANTS:
            return SCENE_FALLBACK_VARIANTS[key]
    return (text, text, text, text, text)


def _must_include_literals(case: Mapping[str, Any]) -> list[str]:
    control = _control(case)
    raw = control.get("must_include") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _pick_variant(case: Mapping[str, Any], offset: int) -> str:
    original = str(case.get("user_message") or "").strip()
    pool = _variant_pool(case)
    variant = pool[offset % len(pool)] if pool else original
    literals = _must_include_literals(case)
    if literals and not all(literal in variant for literal in literals):
        matching = [item for item in pool if all(literal in item for literal in literals)]
        if matching:
            return matching[offset % len(matching)]
        return original
    return variant


def expand_cases(seed_cases: list[dict[str, Any]], *, variants_per_seed: int = 5, target_count: int | None = None) -> list[dict[str, Any]]:
    if variants_per_seed < 1:
        raise ValueError("variants_per_seed must be >= 1")
    expanded: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for seed_index, seed in enumerate(seed_cases, start=1):
        base_id = str(seed.get("case_id") or f"seed_{seed_index:03d}").strip()
        for variant_index in range(variants_per_seed):
            item = copy.deepcopy(seed)
            item["case_id"] = base_id if variant_index == 0 else f"{base_id}__v{variant_index + 1:02d}"
            item["user_message"] = _pick_variant(seed, variant_index)
            _ensure_style_anchor(item)
            if item["case_id"] in seen_ids:
                raise ValueError(f"duplicate case_id after expansion: {item['case_id']}")
            seen_ids.add(str(item["case_id"]))
            expanded.append(item)
    if target_count is not None:
        if target_count < 1:
            raise ValueError("target_count must be >= 1")
        expanded = expanded[:target_count]
    return expanded


def build_summary(seed_cases: list[dict[str, Any]], expanded_cases: list[dict[str, Any]], *, seed_path: Path, out_dir: Path) -> dict[str, Any]:
    scene_counts = Counter()
    source_messages = set()
    fallback_messages: list[str] = []
    for case in seed_cases:
        text = str(case.get("user_message") or "").strip()
        source_messages.add(text)
        control = _control(case)
        scene = str(control.get("task_mode") or control.get("response_move") or "unknown")
        scene_counts[scene] += 1
        if text not in MESSAGE_VARIANTS:
            fallback_messages.append(text)
    expanded_scene_counts = Counter(str(_control(case).get("task_mode") or _control(case).get("response_move") or "unknown") for case in expanded_cases)
    return {
        "seed_path": str(seed_path),
        "output_dir": str(out_dir),
        "seed_cases": len(seed_cases),
        "expanded_cases": len(expanded_cases),
        "unique_seed_user_messages": len(source_messages),
        "unique_expanded_user_messages": len({str(case.get("user_message") or "").strip() for case in expanded_cases}),
        "manual_variant_coverage": len(source_messages - set(fallback_messages)),
        "fallback_seed_messages": sorted(set(fallback_messages)),
        "seed_scene_counts": dict(sorted(scene_counts.items())),
        "expanded_scene_counts": dict(sorted(expanded_scene_counts.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expand the isolated surface daily-goal seed set into deterministic variants.")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED, help="Seed JSONL case file.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory.")
    parser.add_argument("--target-count", type=int, default=500, help="Number of cases to write.")
    parser.add_argument("--variants-per-seed", type=int, default=5, help="Deterministic variants generated per seed row.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cases.jsonl and summary.json.")
    args = parser.parse_args(argv)

    seed_path = args.seed.resolve()
    out_dir = args.out.resolve()
    cases_path = out_dir / "cases.jsonl"
    summary_path = out_dir / "summary.json"
    if (cases_path.exists() or summary_path.exists()) and not args.force:
        raise FileExistsError(f"{out_dir} already contains generated files; pass --force to overwrite")

    seed_cases = read_jsonl(seed_path)
    expanded = expand_cases(seed_cases, variants_per_seed=args.variants_per_seed, target_count=args.target_count)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(cases_path, expanded)
    summary = build_summary(seed_cases, expanded, seed_path=seed_path, out_dir=out_dir)
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
