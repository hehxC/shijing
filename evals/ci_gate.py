"""CI 门禁辅助：评测跑完后按"绝对阈值 + 基线对比"决定进程退出码。

供评测 runner 复用：
    - --fail-below <阈值>：主指标低于阈值直接失败；
    - --compare <基线文件> + --max-regression <降幅>：主指标相对基线下降超过允许幅度则失败。
门禁结果通过退出码表达（0 = 通过，1 = 失败），CI 据此拦截。
"""

import json
from pathlib import Path


def apply_gate(
    metrics: dict,
    *,
    primary: str,
    fail_below: float | None,
    compare: Path | None,
    max_regression: float | None,
) -> bool:
    """执行门禁检查，返回是否通过；不通过时打印原因。"""
    value = metrics[primary]
    passed = True

    # 绝对阈值：主指标低于下限即失败
    if fail_below is not None and value < fail_below:
        print(f"门禁失败：{primary}={value:.1%} 低于阈值 {fail_below:.1%}")
        passed = False

    # 基线对比：相对上次基线下降超过允许幅度即失败（基线文件不存在时跳过）
    if compare is not None and max_regression is not None:
        if compare.is_file():
            baseline = json.loads(compare.read_text(encoding="utf-8"))
            baseline_value = baseline.get(primary)
            if baseline_value is None:
                print(f"警告：基线文件 {compare} 缺少主指标 {primary}，跳过对比")
            else:
                delta = value - baseline_value
                if delta < -max_regression:
                    print(
                        f"门禁失败：{primary}={value:.1%}，相对基线 {baseline_value:.1%} "
                        f"下降 {delta:.1%}（允许下降 {max_regression:.1%}）"
                    )
                    passed = False
                else:
                    print(f"基线对比通过：{primary}={value:.1%}，基线 {baseline_value:.1%}，delta {delta:+.1%}")
        else:
            print(f"提示：基线文件 {compare} 不存在，跳过基线对比（首次运行可先建立基线）")

    return passed
