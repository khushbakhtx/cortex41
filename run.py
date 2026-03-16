#!/usr/bin/env python3
"""
cortex41 Desktop Agent — CLI entry point.

Usage:
  python run.py "open Safari and search for cortex41"
  python run.py          (interactive mode, type goals one by one)

macOS permissions required (one-time setup):
  System Settings > Privacy & Security > Accessibility    → allow Terminal / python
  System Settings > Privacy & Security > Screen Recording → allow Terminal / python

Move mouse to top-left corner to trigger safety abort (pyautogui FAILSAFE).
"""

import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

# ── ANSI colors ────────────────────────────────────────────────────────────────
R = "\033[0m"
COLORS = {
    "thinking":    "\033[90m",
    "plan":        "\033[94m",
    "subtask":     "\033[33m",
    "subtask_done":"\033[32m",
    "action":      "\033[32m",
    "success":     "\033[92m",
    "error":       "\033[91m",
    "info":        "\033[90m",
    "stuck":       "\033[93m",
    "stats":       "",
}

step_count = 0


async def on_event(payload: dict):
    global step_count
    t = payload.get("type", "")
    msg = payload.get("message", "")
    data = payload.get("data")

    if t in ("screenshot", "pong", "stats"):
        return

    c = COLORS.get(t, "")

    if t == "plan" and data and "sub_tasks" in data:
        print(f"\n{c}PLAN ({len(data['sub_tasks'])} steps):{R}")
        for task in data["sub_tasks"]:
            print(f"  {c}{task['id']}.{R} {task['description']}")
        print()
        return

    if t == "subtask":
        print(f"\n{c}── {msg}{R}")
        return

    if t == "subtask_done":
        print(f"{c}   {msg}{R}")
        return

    if t == "action" and data:
        step = data.get("step", "?")
        action_type = data.get("type", "?")
        confidence = data.get("confidence")
        progress = data.get("goal_progress", "")
        reasoning = (data.get("raw_reasoning") or "").strip()

        conf_str = f" {int(confidence*100)}%" if confidence else ""
        prog_str = f" [{progress}]" if progress else ""

        coord_str = ""
        if action_type == "click":
            coord_str = f" at ({data.get('x', '?')}, {data.get('y', '?')})"
        elif action_type == "type":
            target_x, target_y = data.get("x"), data.get("y")
            if target_x is not None:
                coord_str = f" at ({target_x}, {target_y})"

        print(f"{c}  [{step}] {action_type}{conf_str}{prog_str}{coord_str}{R}  {msg}")

        # Print first 3 non-empty reasoning lines, indented
        if reasoning:
            lines = [l.strip() for l in reasoning.split("\n") if l.strip()][:3]
            for line in lines:
                print(f"\033[90m       {line[:110]}{R}")
        return

    if t == "success":
        print(f"\n{c}DONE  {msg}{R}\n")
        return

    if t == "error":
        print(f"\n{c}ERR   {msg}{R}\n")
        return

    if t == "thinking":
        print(f"{c}...   {msg}{R}")
        return

    if t == "info" and msg:
        print(f"\033[90m      {msg}{R}")
        return

    if msg:
        print(f"{c}[{t}] {msg}{R}")


async def main():
    from backend.agent.cortex41_agent import Cortex41AgentRunner

    print("\033[33m cortex41 desktop agent\033[0m")
    print("\033[90m move mouse to top-left corner to abort at any time\033[0m\n")

    runner = Cortex41AgentRunner(session_id="cli", websocket_send_fn=on_event)

    try:
        await runner.initialize()
    except RuntimeError as e:
        print(f"\033[91mSetup error: {e}\033[0m")
        print("\033[90mFix: System Settings > Privacy & Security > Screen Recording → allow Terminal\033[0m")
        sys.exit(1)

    if len(sys.argv) > 1:
        goal = " ".join(sys.argv[1:])
        print(f"\033[33mGoal: {goal}\033[0m\n")
        await runner.run_goal(goal)
    else:
        print("Enter a goal and press Enter. Empty line to quit.\n")
        loop = asyncio.get_event_loop()
        while True:
            try:
                goal = await loop.run_in_executor(None, lambda: input("\033[33m> \033[0m").strip())
                if not goal:
                    break
                await runner.run_goal(goal)
                print()
            except (KeyboardInterrupt, EOFError):
                print("\nBye.")
                break

    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
