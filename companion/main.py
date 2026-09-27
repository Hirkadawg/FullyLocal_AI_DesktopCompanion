"""CLI entry point.

Intentionally thin: everything of substance lives in `Companion`, so the GUI and
voice frontends in later phases reuse it instead of reimplementing the pipeline.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from core.companion import Companion, build_companion
from core.config import AppConfig
from core.errors import CompanionError, LLMUnavailable, PrivacyBlocked
from core.logging import get_logger, setup_logging
from core.types import ScreenContext
from modules.capture.screen import list_monitors

__version__ = "1.0.0"

log = get_logger(__name__)

HELP = """
Commands:
  <question>    ask about what is on screen
  :screen       print the raw text the last capture produced
  :timings      per-stage timings from the last capture
  :windows      windows the privacy guard can currently see
  :help         this text
  exit / quit   leave
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="companion",
        description="Local AI desktop companion (ask about your screen)",
    )
    default_config = Path(__file__).parent / "config.yaml"
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument(
        "--list-monitors", action="store_true", help="print the monitor table and exit"
    )
    parser.add_argument(
        "--shot",
        metavar="PATH",
        help="save a screenshot of the configured monitor and exit (no OCR, no model)",
    )
    parser.add_argument(
        "--image",
        metavar="PATH",
        help="read this image instead of the live screen (reproducible testing)",
    )
    parser.add_argument(
        "--ask", metavar="TEXT", help="ask one question, print the answer, exit"
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="run the always-on-top window instead of the terminal loop",
    )
    parser.add_argument(
        "--hear",
        metavar="SECONDS",
        nargs="?",
        const=20.0,
        type=float,
        help="listen to system audio for N seconds (default 20) and print the "
        "rolling transcript -- play a video while it runs",
    )
    parser.add_argument(
        "--listen",
        metavar="SECONDS",
        nargs="?",
        const=5.0,
        type=float,
        help="record from the microphone for N seconds (default 5), transcribe, "
        "and print the result",
    )
    parser.add_argument(
        "--say",
        metavar="TEXT",
        help="speak TEXT and exit -- checks the voice pipeline on its own",
    )
    parser.add_argument(
        "--list-audio",
        action="store_true",
        help="list audio output devices and exit",
    )
    parser.add_argument(
        "--watch",
        metavar="SECONDS",
        type=float,
        help="run the ambient watch loop for N seconds and report what it cost, "
        "without involving the model",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="run each perception source separately and report what it got, "
        "without sending anything to the model",
    )
    parser.add_argument(
        "--window",
        metavar="TEXT",
        help="read the window whose title or process contains TEXT, instead of "
        "choosing automatically (overrides capture.window_match)",
    )
    parser.add_argument(
        "--export-liked",
        metavar="DIR",
        nargs="?",
        const="",
        help="write every thumbs-up answer and remark as a training example, for "
        "review, to DIR (default data/exports) and exit -- nothing is trained",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", help="with --export-liked: only from this day on"
    )
    parser.add_argument(
        "--kind", choices=("answer", "remark"), help="with --export-liked: only this kind"
    )
    parser.add_argument(
        "--analyse-music",
        metavar="FILE",
        help="print the key, tempo and chords of an audio file and exit (nothing is uploaded)",
    )
    parser.add_argument(
        "--start", type=float, default=0.0, metavar="S", help="with --analyse-music: from this second"
    )
    parser.add_argument(
        "--seconds", type=float, default=30.0, metavar="S", help="with --analyse-music: this much of it"
    )
    parser.add_argument("--debug", action="store_true", help="verbose logs and timings")
    parser.add_argument("--version", action="version", version=f"companion {__version__}")
    args = parser.parse_args(argv)

    if args.list_monitors:
        setup_logging("WARNING")
        return _print_monitors(args.config)

    try:
        config = AppConfig.load(args.config)
    except CompanionError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        "DEBUG" if args.debug else config.logging.level,
        log_file=config.root / config.logging.file if config.logging.file else None,
    )

    if args.window:
        config.capture.window_match = args.window

    if args.export_liked is not None:
        return _export_liked(config, args.export_liked or None, args.since, args.kind)

    if args.analyse_music:
        return _analyse_music(args.analyse_music, args.start, args.seconds)

    if args.shot:
        return _save_shot(config, args.shot)

    if args.probe:
        return _probe(config, args.image)

    if args.gui:
        return _run_gui(config, args.image)

    if args.list_audio:
        return _list_audio()

    if args.say:
        return _say(config, args.say)

    if args.listen:
        return _listen(config, args.listen)

    if args.hear:
        return _hear(config, args.hear)

    if args.watch:
        return _watch(config, args.watch)

    try:
        companion = build_companion(config, image_path=args.image)
    except CompanionError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 2

    try:
        companion.llm.health_check()
    except LLMUnavailable as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 3

    try:
        if args.ask:
            return _one_shot(companion, args.ask)
        return _repl(companion, config, replaying=bool(args.image))
    finally:
        companion.close()


def _print_monitors(config_path: Path) -> int:
    configured = None
    try:
        configured = AppConfig.load(config_path).monitor_index
    except CompanionError:
        pass

    print("\nmss monitor table (index 0 = all monitors combined):\n")
    for i, m in enumerate(list_monitors()):
        marker = "  <- config monitor_index" if i == configured else ""
        print(
            f"  [{i}] {m['width']:>5} x {m['height']:<5} "
            f"at ({m['left']}, {m['top']}){marker}"
        )
    print(
        "\nThis ordering follows Windows' internal enumeration and may differ from\n"
        "Display Settings. Confirm with:  python main.py --shot check.png\n"
    )
    return 0


def _save_shot(config: AppConfig, path: str) -> int:
    from core.privacy import PrivacyGuard
    from core.winapi import visible_windows
    from modules.capture.screen import MSSCapture

    screen = MSSCapture(monitor_index=config.monitor_index)
    guard = PrivacyGuard(
        enabled=config.privacy.enabled,
        blocked_processes=config.privacy.blocked_processes,
        blocked_title_patterns=config.privacy.blocked_title_patterns,
    )
    try:
        block = guard.check(visible_windows(within=screen.bounds()))
        if block is not None:
            print(f"Blocked: {block.reason}\n  window: {block.window}", file=sys.stderr)
            return 4
        image = screen.grab()
    except CompanionError as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return 2
    finally:
        screen.close()

    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(f"Saved {image.width}x{image.height} capture of monitor "
          f"{config.monitor_index} to {out}")
    return 0


def _export_liked(config: AppConfig, out: str | None, since: str | None, kind: str | None) -> int:
    """Liked replies to a training file and a review file. Trains nothing."""
    from datetime import date

    from core.export import export_liked

    try:
        since_day = date.fromisoformat(since) if since else None
    except ValueError:
        print(f"--since must look like 2026-09-14, not {since!r}", file=sys.stderr)
        return 2
    result = export_liked(config, out_dir=out, since=since_day, kind=kind)
    if not result.examples:
        print("No thumbs-up replies to export yet. Rate some with the thumbs in the window.")
        return 0
    print(f"Exported {result.answers} answer(s) and {result.remarks} remark(s).")
    print(f"  training examples: {result.jsonl}")
    print(f"  read them first:   {result.review}")
    print("Nothing was trained, and nothing left this machine.")
    return 0


def _analyse_music(path: str, start: float, seconds: float) -> int:
    """The music analysis on a file: the same one a spoken question gets."""
    import time

    from modules.audio.music import analyse, decode_file, describe

    try:
        audio = decode_file(path, start_s=start, seconds=seconds)
    except Exception as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 1
    started = time.perf_counter()
    text = describe(analyse(audio, 16000))
    print(text)
    print(f"\n({(time.perf_counter() - started) * 1000:.0f} ms; analysed on this machine)")
    return 0


def _run_gui(config: AppConfig, image_path: str | None) -> int:
    """Launch the windowed interface.

    The model check happens on the worker thread rather than here, so a missing
    model surfaces inside the window instead of on a terminal nobody is looking
    at.
    """
    try:
        from modules.ui.app import CompanionApp
    except ImportError as exc:
        print(
            f"The GUI needs PySide6, which isn't installed ({exc}).\n"
            "  Fix with:  .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2
    return CompanionApp(config, image_path=image_path).run()


def _list_audio() -> int:
    try:
        import sounddevice as sd
    except ImportError as exc:
        print(f"sounddevice not installed ({exc})", file=sys.stderr)
        return 2

    default = sd.default.device[1]
    print("\n  Output devices (use the index or part of the name as voice.device):\n")
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] < 1:
            continue
        mark = "->" if index == default else "  "
        print(f"   {mark} [{index}] {dev['name']}  "
              f"{dev['max_output_channels']}ch @ {dev['default_samplerate']:.0f} Hz")

    from modules.voice.devices import input_devices, microphone_names, resolve

    devices = input_devices()
    default_input = sd.default.device[0]
    print("\n  Microphones (choose one in Settings > Voice, or put its name in "
          "speech.input_device):\n")
    for name in microphone_names(devices):
        index = resolve(name, devices)
        mark = "->" if index == default_input else "  "
        print(f"   {mark} [{index}] {name}")
    print()
    return 0


def _hear(config: AppConfig, seconds: float) -> int:
    """Listen to system audio and report the transcript and what it cost."""
    from core.companion import build_audio

    if not config.audio.enabled:
        print(
            "System audio is off. Set `audio.enabled: true` in config.yaml.\n"
            "  It transcribes everything playing through your speakers, which is\n"
            "  why it is opt-in.",
            file=sys.stderr,
        )
        return 2

    started = time.perf_counter()
    try:
        transcriber = build_audio(config)
    except CompanionError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 7
    print(f"  ready in {(time.perf_counter() - started) * 1000:.0f} ms "
          f"({config.audio.model}, {config.audio.chunk_seconds:.0f}s chunks)")
    print(f"\n  Listening for {seconds:.0f}s — play a video now.\n")

    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    seen = ""
    try:
        while time.perf_counter() - wall_start < seconds:
            time.sleep(1.0)
            current = transcriber.transcript(minutes=60, max_chars=100_000)
            if len(current) > len(seen):
                print(f"   [{time.perf_counter() - wall_start:5.1f}s] "
                      f"{current[len(seen):].strip()[:100]}")
                seen = current
    except KeyboardInterrupt:
        print("\n  stopped early")

    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    print(f"\n  chunks seen        {transcriber.chunks_seen}")
    print(f"  chunks with sound  {transcriber.chunks_transcribed}")
    print(f"  buffered audio     {transcriber.capture.seconds_buffered:.1f}s")
    print(f"  CPU used           {cpu:.2f}s of {wall:.0f}s "
          f"= {100 * cpu / wall:.1f}% of one core")
    if not seen:
        print("\n  Nothing was heard. Play something audible, and check that\n"
              "  audio.output_device matches the speaker actually in use.")
    transcriber.stop()
    transcriber.capture.stop()
    print()
    return 0


def _listen(config: AppConfig, seconds: float) -> int:
    """Record, transcribe, and report timings. No model, no GUI."""
    from core.companion import build_stt
    from modules.voice.microphone import Recorder

    if not config.speech.enabled:
        print("Listening is disabled (speech.enabled: false).", file=sys.stderr)
        return 2

    started = time.perf_counter()
    try:
        stt = build_stt(config)
    except CompanionError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 6
    print(f"  speech model ready in {(time.perf_counter() - started) * 1000:.0f} ms "
          f"({config.speech.model}, {config.speech.device}/{config.speech.compute_type})")

    recorder = Recorder(
        sample_rate=stt.sample_rate,
        device=config.speech.input_device,
        max_seconds=config.speech.max_seconds,
    )
    try:
        recorder.start()
    except CompanionError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 6

    print(f"\n  Listening for {seconds:.0f}s — speak now.")
    time.sleep(seconds)
    audio = recorder.stop()
    captured = len(audio) / stt.sample_rate
    peak = float(abs(audio).max()) if len(audio) else 0.0
    print(f"  captured {captured:.1f}s, peak level {peak:.3f}")
    if peak < 0.01:
        print("  (that is near silence — check the input device with --list-audio)")

    started = time.perf_counter()
    text = stt.transcribe(audio)
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  transcribed in {elapsed:.0f} ms\n")
    print(f"  heard: {text!r}\n" if text else "  heard nothing intelligible\n")
    stt.close()
    return 0


def _say(config: AppConfig, text: str) -> int:
    """Speak one line, timing how long the first audio takes to arrive."""
    from core.companion import build_speaker

    if not config.voice.enabled:
        print("Voice is disabled (voice.enabled: false in config.yaml).",
              file=sys.stderr)
        return 2

    started = time.perf_counter()
    try:
        speaker = build_speaker(config)
    except CompanionError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 5
    load_ms = (time.perf_counter() - started) * 1000

    print(f"  voice ready in {load_ms:.0f} ms "
          f"({config.voice.voice} @ {speaker.engine.sample_rate} Hz)")

    started = time.perf_counter()
    speaker.feed(text)
    speaker.flush()

    # Wait for the first audio to actually reach the device.
    while not speaker.player.is_playing and speaker.is_speaking:
        time.sleep(0.005)
    first_ms = (time.perf_counter() - started) * 1000
    print(f"  first audio after {first_ms:.0f} ms")

    speaker.player.wait_until_idle(timeout=120)
    time.sleep(0.2)  # let the last buffer drain before closing the device
    speaker.close()
    print("  done\n")
    return 0


def _watch(config: AppConfig, seconds: float) -> int:
    """Run the ambient loop headlessly and report what it cost.

    Reports process CPU time as a share of one core, which is the number the
    "runs efficiently" requirement actually turns on. The model is never
    contacted, so this measures perception alone.
    """
    from core.companion import build_companion

    companion = build_companion(config)
    if companion.ambient is None:
        print("Ambient watching is disabled (ambient.enabled: false).", file=sys.stderr)
        return 2

    print(f"\n  Watching monitor {config.monitor_index} for {seconds:.0f}s. "
          f"Switch windows or scroll to see it react. Ctrl-C to stop early.\n")
    print(f"  sample every {config.ambient.sample_interval_s}s · "
          f"settle {config.ambient.stable_delay_s}s · "
          f"threshold {config.ambient.change_threshold}\n")

    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    deadline = wall_start + seconds
    reads = 0

    try:
        while time.perf_counter() < deadline:
            tick_started = time.perf_counter()
            context = companion.ambient_tick()
            if context is not None:
                reads += 1
                where = context.window_title or context.app_name or "screen"
                print(f"   [{time.perf_counter() - wall_start:6.1f}s] re-read "
                      f"{len(context.text):>6} chars via {context.source:<4} "
                      f"from {where[:44]}")
            slack = config.ambient.sample_interval_s - (
                time.perf_counter() - tick_started
            )
            if slack > 0:
                time.sleep(slack)
    except KeyboardInterrupt:
        print("\n  stopped early")
    finally:
        companion.close()

    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    stats = companion.ambient.stats

    print(f"\n  ran {wall:.1f}s")
    print(f"  samples          {stats.samples}")
    print(f"  changes seen     {stats.changes}")
    print(f"  full re-reads    {reads}")
    print(f"  mean sample cost {stats.mean_sample_ms:.1f} ms")
    print(f"  CPU used         {cpu:.2f}s of {wall:.1f}s wall "
          f"= {100 * cpu / wall:.1f}% of one core")
    print("\n  Target is under 2% CPU on a static screen.\n")
    return 0


def _probe(config: AppConfig, image_path: str | None) -> int:
    """Time each perception source separately and report what it produced.

    Prints counts, timings and a short preview -- enough to tell whether a
    source worked, without dumping the whole screen into the terminal.
    """
    from core.companion import build_perception_sources
    from core.privacy import PrivacyGuard
    from core.winapi import visible_windows
    from modules.capture.screen import (
        MSSCapture,
        StaticImageSource,
        WindowCapture,
    )

    if image_path:
        screen = StaticImageSource(image_path)
    else:
        factory = WindowCapture if config.capture.mode == "window" else MSSCapture
        screen = factory(
            monitor_index=config.monitor_index,
            ignore_processes=config.capture.ignore_processes,
            min_window_on_monitor=config.capture.min_window_on_monitor,
            window_match=config.capture.window_match,
        )

    if screen.is_live:
        guard = PrivacyGuard(
            enabled=config.privacy.enabled,
            blocked_processes=config.privacy.blocked_processes,
            blocked_title_patterns=config.privacy.blocked_title_patterns,
        )
        block = guard.check(visible_windows(within=screen.bounds()))
        if block is not None:
            print(f"Blocked: {block.reason}\n  window: {block.window}", file=sys.stderr)
            return 4

        target = screen.target_window()
        print(f"\n  monitor {config.monitor_index}  bounds={screen.bounds()}")
        print(f"  capture mode: {config.capture.mode}")
        print("\n  windows on this monitor, topmost first:")
        for cand in screen.candidates():
            mark = "->" if target and cand.window.hwnd == target.hwnd else "  "
            note = "" if cand.viable else f"  (skipped: {cand.reason})"
            print(
                f"   {mark} {cand.monitor_share:>5.1%} of monitor  "
                f"{cand.window.process:<20} {cand.window.title[:40]!r}{note}"
            )
        print(f"\n  reading: {target if target else '(no suitable window found)'}")
    else:
        print(f"\n  replay: {image_path}")

    sources = build_perception_sources(config, screen, replaying=bool(image_path))
    print(f"  chain: {' -> '.join(s.name for s in sources)}   "
          f"(min_chars={config.perception.min_chars})\n")

    for source in sources:
        started = time.perf_counter()
        try:
            context = source.read()
        except Exception as exc:
            print(f"  {source.name:<6} FAILED  {type(exc).__name__}: {exc}")
            continue
        elapsed = (time.perf_counter() - started) * 1000
        enough = "OK " if len(context.text.strip()) >= config.perception.min_chars else "thin"
        preview = " ".join(context.text.split())[:90]
        print(f"  {source.name:<6} {enough}  {len(context.text):>6} chars  "
              f"{elapsed:>7.0f} ms")
        if context.timings_ms:
            detail = "  ".join(f"{k} {v:.0f}ms" for k, v in context.timings_ms.items())
            print(f"         stages: {detail}")
        print(f"         preview: {preview or '(nothing)'}...\n")

    for source in sources:
        source.close()
    close = getattr(screen, "close", None)
    if callable(close):
        close()
    return 0


def _status_line(context: ScreenContext) -> str:
    timings = "  ".join(f"{k} {v:.0f}ms" for k, v in context.timings_ms.items())
    return f"  [{context.source}: {len(context.text)} chars   {timings}]"


def _answer(companion: Companion, question: str) -> None:
    """Observe, then answer. Raises CompanionError for the caller to report."""
    context = companion.observe()
    if context.is_empty:
        print(
            "  I couldn't read any text on that screen. If something is definitely\n"
            "  there, check --list-monitors and confirm with --shot."
        )
        return

    answer = companion.ask(question, context=context)
    print()
    for chunk in answer.chunks:
        print(chunk, end="", flush=True)
    print()
    print(_status_line(context))


def _one_shot(companion: Companion, question: str) -> int:
    try:
        _answer(companion, question)
    except PrivacyBlocked as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 4
    except CompanionError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    return 0


def _repl(companion: Companion, config: AppConfig, replaying: bool) -> int:
    source = "replayed image" if replaying else f"monitor {config.monitor_index}"
    print(f"\n  Local AI Desktop Companion {__version__}")
    print(f"  model: {config.ollama.model}    reading: {source}")
    print(f"  {companion.privacy.describe()}")
    print("  Type a question about your screen, or :help. Ctrl-C to quit.\n")

    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0

        if not question:
            continue
        lowered = question.lower()
        if lowered in {"exit", "quit", ":exit", ":quit"}:
            print("bye.")
            return 0
        if lowered in {":help", "help", "?"}:
            print(HELP)
            continue
        if lowered == ":screen":
            _dump_screen(companion)
            continue
        if lowered == ":timings":
            _dump_timings(companion)
            continue
        if lowered == ":windows":
            _dump_windows(companion)
            continue

        try:
            _answer(companion, question)
        except PrivacyBlocked as exc:
            print(f"\n  {exc}")
            print("  Nothing was captured. Close that window, or adjust "
                  "`privacy` in config.yaml.")
        except LLMUnavailable as exc:
            print(f"\n  {exc}")
        except CompanionError as exc:
            print(f"\n  Error: {exc}")
        except KeyboardInterrupt:
            print("\n  (interrupted)")
        print()


def _dump_screen(companion: Companion) -> None:
    context = companion.last_context
    if context is None:
        print("  Nothing captured yet — ask a question first.")
        return
    print(f"\n--- screen text ({len(context.text)} chars, {context.source}) ---")
    print(context.text or "(empty)")
    print("--- end ---")


def _dump_timings(companion: Companion) -> None:
    context = companion.last_context
    if context is None:
        print("  Nothing captured yet — ask a question first.")
        return
    if not context.timings_ms:
        print("  No timings recorded.")
        return
    total = sum(context.timings_ms.values())
    for name, ms in context.timings_ms.items():
        print(f"  {name:<12} {ms:>8.1f} ms")
    print(f"  {'total':<12} {total:>8.1f} ms")


def _dump_windows(companion: Companion) -> None:
    from core.winapi import visible_windows

    if not companion.screen.is_live:
        print("  Replay mode — no live windows.")
        return
    windows = visible_windows(within=companion.screen.bounds())
    if not windows:
        print("  No visible windows found on this monitor.")
        return
    print(f"\n  {len(windows)} visible window(s) on this monitor:")
    for window in windows:
        flag = "*" if window.is_foreground else " "
        print(f"   {flag} {window.process:<24} {window.title[:70]}")
    block = companion.privacy.check(windows)
    print(f"\n  privacy: {'BLOCKED — ' + block.reason if block else 'clear'}")


if __name__ == "__main__":
    sys.exit(main())
