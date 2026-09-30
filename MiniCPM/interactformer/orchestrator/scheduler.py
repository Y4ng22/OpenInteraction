"""
Micro-Turn Scheduler: Manages the 200ms interaction rhythm.

The scheduler is the "heartbeat" of InteractFormer. It drives the
200ms micro-turn cycle that structures all interaction. Each tick:

1. Collects incoming audio/video input
2. Triggers Interaction Model processing
3. Checks for pending Background Model results
4. Dispatches output (speech, text, actions)
5. Updates session metrics

This is the explicit realization of TML's micro-turn concept —
not just a conceptual description but an actual scheduling mechanism.

The scheduler operates at three levels:
- Micro (200ms): individual processing ticks
- Meso (utterance): groups of micro-turns forming a speech segment
- Macro (session): the full interaction session
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any
from enum import Enum
from collections import deque


class TickPhase(Enum):
    """Phases within a single scheduler tick."""
    INPUT_COLLECT = "input_collect"      # Gather audio/video from streams
    ENCODE = "encode"                    # Multimodal encoding
    THINK = "think"                      # Thinker processing
    BRIDGE_CHECK = "bridge_check"        # Check for S2 results
    DECIDE = "decide"                    # Decide to speak/delegate
    TALK = "talk"                        # Talker generation (if speaking)
    OUTPUT_DISPATCH = "output_dispatch"  # Send output to client


@dataclass
class SchedulerConfig:
    """Configuration for the micro-turn scheduler.

    Attributes:
        tick_duration_ms: Duration of each micro-turn (200ms per TML).
        max_ticks_per_burst: Max ticks to process in a burst before
            yielding to other threads.
        background_check_interval_ticks: How often to check for S2 results.
        latency_budget_ms: Maximum latency budget per tick. If processing
            exceeds this, quality is degraded for speed.
        stats_window_ticks: Number of ticks over which to average stats.
    """
    tick_duration_ms: int = 200
    max_ticks_per_burst: int = 10
    background_check_interval_ticks: int = 5  # Every 1 second
    latency_budget_ms: int = 150  # Must be < tick_duration_ms
    stats_window_ticks: int = 125  # 25 seconds


@dataclass
class TickStats:
    """Statistics for a single tick."""
    tick_id: int
    phase_latencies: Dict[str, float] = field(default_factory=dict)
    total_latency_ms: float = 0.0
    exceeded_budget: bool = False
    background_injections: int = 0


class MicroTurnScheduler:
    """Scheduler that drives the 200ms micro-turn rhythm.

    This is the "heartbeat" of InteractFormer. Each tick of the
    scheduler corresponds to one micro-turn of interaction.

    The scheduler can operate in two modes:
    - Real-time: ticks are driven by actual wall-clock time (200ms apart)
    - Batch: ticks are processed as fast as possible (for offline processing)

    In real-time mode, if processing takes longer than the tick duration,
    the scheduler adapts by:
    1. Reducing Talker quality (fewer ODE steps)
    2. Skipping non-critical bridge checks
    3. Queuing overflow input for the next tick
    """

    def __init__(
        self,
        config: Optional[SchedulerConfig] = None,
    ):
        self.config = config or SchedulerConfig()

        # Tick counter
        self._tick_id: int = 0

        # Registered callbacks for each phase
        self._callbacks: Dict[TickPhase, list[Callable]] = {
            phase: [] for phase in TickPhase
        }

        # Statistics
        self._recent_stats: deque[TickStats] = deque(
            maxlen=self.config.stats_window_ticks
        )
        self._total_ticks: int = 0
        self._total_overruns: int = 0

        # Runtime control
        self._running: bool = False
        self._paused: bool = False
        self._scheduler_thread: Optional[threading.Thread] = None

    @property
    def tick_id(self) -> int:
        return self._tick_id

    @property
    def average_latency_ms(self) -> float:
        """Average tick latency over the stats window."""
        if not self._recent_stats:
            return 0.0
        return sum(s.total_latency_ms for s in self._recent_stats) / len(self._recent_stats)

    @property
    def overrun_rate(self) -> float:
        """Fraction of ticks that exceeded the latency budget."""
        if self._total_ticks == 0:
            return 0.0
        return self._total_overruns / self._total_ticks

    def register_callback(
        self, phase: TickPhase, callback: Callable
    ) -> None:
        """Register a callback for a specific tick phase.

        Callbacks are called in order of registration during each tick.
        Each callback receives the tick_id and returns a dict with
        phase-specific data.

        Args:
            phase: Which phase to register for.
            callback: Callable(tick_id: int) -> dict.
        """
        self._callbacks[phase].append(callback)

    def tick(self) -> TickStats:
        """Execute one complete micro-turn.

        Runs through all phases in order, calling registered callbacks.
        Tracks latency at each phase and reports if the latency budget
        is exceeded.

        Returns:
            TickStats for this tick.
        """
        tick_start = time.time()
        stats = TickStats(tick_id=self._tick_id)
        phase_timings = {}

        for phase in TickPhase:
            phase_start = time.time()

            for callback in self._callbacks[phase]:
                try:
                    callback(self._tick_id)
                except Exception as e:
                    # Log error but continue
                    print(f"[Scheduler] Error in {phase.value}: {e}")

            phase_latency = (time.time() - phase_start) * 1000
            phase_timings[phase.value] = phase_latency

        stats.phase_latencies = phase_timings
        stats.total_latency_ms = (time.time() - tick_start) * 1000
        stats.exceeded_budget = (
            stats.total_latency_ms > self.config.latency_budget_ms
        )

        # Update tracking
        self._recent_stats.append(stats)
        self._total_ticks += 1
        if stats.exceeded_budget:
            self._total_overruns += 1

        self._tick_id += 1
        return stats

    def start_realtime(self) -> None:
        """Start the scheduler in real-time mode.

        Launches a background thread that fires ticks at 200ms intervals.
        """
        self._running = True
        self._scheduler_thread = threading.Thread(
            target=self._realtime_loop,
            name="MicroTurnScheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=2.0)
            self._scheduler_thread = None

    def pause(self) -> None:
        """Pause scheduling (e.g., when no user input)."""
        self._paused = True

    def resume(self) -> None:
        """Resume scheduling."""
        self._paused = False

    def _realtime_loop(self) -> None:
        """Main real-time scheduling loop.

        Runs in a background thread, firing ticks at 200ms intervals.
        If a tick takes longer than 200ms, the next tick starts
        immediately (no backlog accumulation).
        """
        next_tick_time = time.time()

        while self._running:
            if self._paused:
                time.sleep(0.1)
                next_tick_time = time.time()
                continue

            # Wait until the next tick is due
            now = time.time()
            if now < next_tick_time:
                time.sleep(next_tick_time - now)

            # Execute tick
            self.tick()

            # Schedule next tick
            next_tick_time = max(
                time.time(),
                next_tick_time + self.config.tick_duration_ms / 1000,
            )

    def run_batch(self, num_ticks: int) -> list[TickStats]:
        """Run a batch of ticks as fast as possible.

        Args:
            num_ticks: Number of ticks to run.

        Returns:
            List of TickStats for each tick.
        """
        stats_list = []
        for _ in range(num_ticks):
            stats = self.tick()
            stats_list.append(stats)
        return stats_list
