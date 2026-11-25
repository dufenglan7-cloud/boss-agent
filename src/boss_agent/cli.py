# 命令行计划入口；文件输入与 HTTP 入口共用 Agent 能力，结束时刷新监测。
import argparse
from pathlib import Path
from uuid import uuid4
from .agents import Agents
from .channels import JsonJobSource
from .llm import JsonModel
from .storage import Store
from .workflow import Workflow
from .observability import shutdown

# 解析文件参数并运行完整计划；无论成功或失败都尝试关闭监测。
def main():
    parser = argparse.ArgumentParser(description="Generate job application plans; never sends real applications")
    parser.add_argument("--mode", choices=["demo", "llm"], default="demo")
    parser.add_argument("--resume", type=Path, required=True, help="UTF-8 .txt or .md resume")
    parser.add_argument("--requirements", default="", help="Explicit job preferences; optional for resume-led discovery")
    parser.add_argument("--jobs", type=Path, required=True, help="Imported job JSON")
    parser.add_argument("--output", type=Path, default=Path("data/plan.json"))
    parser.add_argument("--db", type=Path, default=Path("data/boss-agent.sqlite3"))
    args = parser.parse_args()
    try:
        agents = Agents(JsonModel() if args.mode == "llm" else None)
        plan = Workflow(agents, JsonJobSource(args.jobs)).run(args.resume.read_text(encoding="utf-8"), args.requirements)
        run_id = str(uuid4())
        Store(args.db).save(run_id, args.mode, plan)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(f"mode={args.mode}; run={run_id}; plan={args.output}; real applications sent=0")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    finally:
        shutdown()

if __name__ == "__main__":
    main()
