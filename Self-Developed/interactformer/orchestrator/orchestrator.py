"""
Orchestrator: Main coordination layer for InteractFormer.

The Orchestrator is the top-level component that ties together the
Interaction Model, Background Model, and Streaming Context Bridge.

It implements the main control loop:
    while session.is_active:
        # 1. Receive streaming input (audio, video, text)
        # 2. Process one micro-turn through Interaction Model
        # 3. Check for Background Model results via Bridge
        # 4. Inject S2 results at appropriate moments
        # 5. Stream output to user

The Orchestrator is also responsible for:
- Starting and stopping sessions
- Managing the interaction rhythm (200ms micro-turns)
- Coordinating S1 ↔ S2 communication
- Handling interruptions and topic changes
- Collecting metrics and telemetry

This is the entry point that external systems (web server, mobile app,
etc.) interact with.
"""

from typing import Optional, Dict, Any, Generator
import time
import threading
import queue

from interactformer.interaction.interaction_model import (
    InteractionModel, MicroTurnOutput,
)
from interactformer.background.background_model import (
    BackgroundModel, BackgroundTask, BackgroundTaskType,
)
from interactformer.bridge.stream_injector import (
    StreamInjector, BridgeMessage, InjectionPriority,
)
from interactformer.bridge.context_packager import (
    ContextPackager, ContextPackage,
)
from interactformer.bridge.cross_attention import (
    StreamingContextBridge,
)
from interactformer.orchestrator.session import (
    StreamingSession, SessionState, SessionConfig,
)
from interactformer.orchestrator.scheduler import (
    MicroTurnScheduler, SchedulerConfig, TickPhase,
)


class Orchestrator:
    """Main orchestrator for InteractFormer.

    Coordinates all components and implements the main interaction loop.

    Usage:
        orchestrator = Orchestrator()
        orchestrator.initialize()

        session = orchestrator.create_session(user_id="user_123")
        session.start()

        # Main loop
        for audio_chunk in audio_stream:
            output = orchestrator.process_micro_turn(
                session_id=session.session_id,
                audio_chunk=audio_chunk,
            )
            if output.speech is not None:
                play_audio(output.speech)

        orchestrator.end_session(session.session_id)
    """

    def __init__(
        self,
        d_model: int = 2048,
        micro_turn_ms: int = 200,
        enable_background: bool = True,
        enable_bridge: bool = True,
        tokenizer: Optional[Any] = None,
        tokenizer_name_or_path: str = "Qwen/Qwen3-Omni-30B-A3B-Instruct",
        trust_remote_code: bool = False,
    ):
        self.d_model = d_model
        self.micro_turn_ms = micro_turn_ms
        self._tokenizer = tokenizer
        self.tokenizer_name_or_path = tokenizer_name_or_path
        self.trust_remote_code = trust_remote_code

        # Core models
        self.interaction_model = InteractionModel(
            d_model=d_model,
            micro_turn_ms=micro_turn_ms,
        )

        self.background_model = (
            BackgroundModel() if enable_background else None
        )

        # Bridge
        self.bridge_injector = (
            StreamInjector(d_model=d_model) if enable_bridge else None
        )
        self.context_packager = (
            ContextPackager() if enable_bridge else None
        )
        self.bridge = (
            StreamingContextBridge(d_model=d_model) if enable_bridge else None
        )

        # Session management
        self._sessions: Dict[str, StreamingSession] = {}
        self._session_runtime: Dict[str, Dict[str, Any]] = {}
        self._session_injectors: Dict[str, StreamInjector] = {}
        self._task_sessions: Dict[str, str] = {}
        # A single model replica/GPU is shared, so mutable runtime state is
        # swapped under this lock.  This prevents concurrent sessions from
        # mixing temporal cells, silence counters, or talker overlap buffers.
        self._model_lock = threading.RLock()
        self._scheduler = MicroTurnScheduler(
            SchedulerConfig(tick_duration_ms=micro_turn_ms)
        )

        # Register scheduler callbacks
        self._scheduler.register_callback(
            TickPhase.BRIDGE_CHECK, self._on_bridge_check
        )

        # Runtime state
        self._initialized: bool = False
        self._running: bool = False

    def initialize(self) -> None:
        """Initialize all components.

        Call this once before creating any sessions.
        """
        if self._initialized:
            return

        self.interaction_model.eval()
        if self.bridge:
            self.bridge.eval()

        # Loading repository-defined Python is opt-in.  A tokenizer does not
        # normally need arbitrary remote code, and services should be able to
        # inject an already-pinned/offline tokenizer.
        tokenizer = self._tokenizer
        if tokenizer is None:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                self.tokenizer_name_or_path,
                trust_remote_code=self.trust_remote_code,
            )
            self._tokenizer = tokenizer
        print("[Orchestrator] HuggingFace tokenizer loaded.")

        # Wire tokenizer into bridge (for semantic S2→S1 encoding)
        # and InteractionModel (for text input tokenization)
        if self.bridge:
            self.bridge.set_tokenizer(
                tokenizer,
                self.interaction_model.thinker.token_embedding,
            )
        self.interaction_model._tokenizer = tokenizer

        # Start background model
        if self.background_model:
            self.background_model.start()

        self._initialized = True
        print("[Orchestrator] Initialized.")

    def shutdown(self) -> None:
        """Shut down all components.

        Call this when the service is stopping.
        """
        self._running = False

        # End all sessions
        for session_id in list(self._sessions.keys()):
            self.end_session(session_id)

        # Stop background model
        if self.background_model:
            self.background_model.stop()

        # Stop scheduler
        self._scheduler.stop()

        self._initialized = False
        print("[Orchestrator] Shut down.")

    def create_session(
        self,
        user_id: Optional[str] = None,
        session_config: Optional[SessionConfig] = None,
    ) -> StreamingSession:
        """Create a new interaction session.

        Args:
            user_id: User identifier.
            session_config: Session configuration.

        Returns:
            New StreamingSession.
        """
        session = StreamingSession(
            user_id=user_id,
            config=session_config,
        )
        with self._model_lock:
            self._sessions[session.session_id] = session
            self._session_runtime[session.session_id] = (
                self.interaction_model.new_runtime_state()
            )
            if self.bridge_injector:
                self._session_injectors[session.session_id] = StreamInjector(
                    d_model=self.d_model,
                    strategy=self.bridge_injector.scheduler.strategy,
                    max_concurrent_streams=self.bridge_injector.max_concurrent_streams,
                )
        return session

    def end_session(self, session_id: str) -> None:
        """End an interaction session.

        Args:
            session_id: Session to end.
        """
        with self._model_lock:
            self._end_session_locked(session_id)

    def _end_session_locked(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return

        # Cancel pending background tasks
        injector = self._session_injectors.pop(session_id, None)
        if injector:
            injector.cancel_topic(reason="session_ended")

        # Clear per-session bridge state
        if self.bridge:
            self.bridge.reset_context(session_id=session_id)

        session.end()
        del self._sessions[session_id]
        self._session_runtime.pop(session_id, None)
        self._task_sessions = {
            task_id: owner for task_id, owner in self._task_sessions.items()
            if owner != session_id
        }

    def process_micro_turn(
        self,
        session_id: str,
        audio_chunk: Optional["torch.Tensor"] = None,
        image: Optional["torch.Tensor"] = None,
        text_input: Optional[str] = None,
    ) -> MicroTurnOutput:
        """Process one isolated, serialized micro-turn for a session."""
        import torch
        with self._model_lock, torch.inference_mode():
            session = self._sessions.get(session_id)
            if session is None:
                raise ValueError(f"Unknown session: {session_id}")
            if not session.is_active:
                return None
            state = self._session_runtime[session_id]
            self.interaction_model.load_runtime_state(state)
            try:
                return self._process_micro_turn_for_session(
                    session_id=session_id,
                    audio_chunk=audio_chunk,
                    image=image,
                    text_input=text_input,
                )
            finally:
                self.interaction_model.save_runtime_state(state)

    def _process_micro_turn_for_session(
        self,
        session_id: str,
        audio_chunk: Optional["torch.Tensor"] = None,
        image: Optional["torch.Tensor"] = None,
        text_input: Optional[str] = None,
    ) -> MicroTurnOutput:
        """Process one micro-turn of interaction.

        This is the main entry point called by external systems for
        each 200ms chunk of streaming input.

        Args:
            session_id: Session identifier.
            audio_chunk: Audio samples for this 200ms window.
            image: Optional image frame (from camera).
            text_input: Optional text input (from keyboard).

        Returns:
            MicroTurnOutput with generated speech, text, and metadata.
        """
        import torch

        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")

        if not session.is_active:
            return None

        # Update session state
        if audio_chunk is not None or text_input is not None:
            session.go_active()
            session.register_user_input()

        # Check for background model results via bridge
        bridge_context = None
        injector = self._session_injectors.get(session_id)
        if self.bridge and injector:
            # 1. Poll S2 for completed/partial results
            self._check_background_results()

            # 2. Get pending S2→S1 injection messages from the queue
            injections = injector.get_context_for_cell(
                cell_id=session.metrics.total_micro_turns,
                is_model_speaking=(
                    session.metrics.total_model_speech_turns >
                    session.metrics.total_user_speech_turns
                ),
            )
            if injections:
                session.register_injection()
                # 3. Convert text content dicts → bridge tensor via BridgeProjector
                new_bridge = self.bridge.embed_content_dicts(
                    injections, device=next(self.interaction_model.parameters()).device
                )
                # 4. Progressive update with freshness metadata
                self.bridge.update_context(
                    new_bridge,
                    session_id=session_id,
                    metadata={
                        "source_micro_turn_id": session.metrics.total_micro_turns,
                        "current_micro_turn_id": session.metrics.total_micro_turns,
                    },
                )

            # 5. Retrieve current per-session bridge state for S1 consumption
            bridge_context = self.bridge.get_current_context(session_id=session_id)

        # Process through Interaction Model
        timestamp_ms = session.metrics.total_micro_turns * self.micro_turn_ms

        output = self.interaction_model.process_micro_turn(
            audio_chunk=audio_chunk,
            image=image,
            text_tokens=text_input,  # Would need tokenization
            background_context=bridge_context,
            timestamp_ms=timestamp_ms,
        )

        # Update session metrics
        session.metrics.total_micro_turns += 1
        if output.cell.is_user_speaking:
            session.metrics.total_user_speech_turns += 1
        if output.cell.is_model_speaking:
            session.metrics.total_model_speech_turns += 1
        if output.should_interrupt:
            session.register_interruption()

        # Handle delegation to Background Model
        if output.should_delegate and self.background_model:
            self._delegate_to_background(session, output)

        # Register model output
        if output.speech is not None:
            session.register_model_output()

        # Check for session expiry
        if session.is_expired() or session.is_idle_timeout():
            session.go_idle()

        return output

    def _delegate_to_background(
        self,
        session: StreamingSession,
        output: MicroTurnOutput,
    ) -> None:
        """Delegate a task to the Background Model.

        Creates a BackgroundTask from the delegation context and
        submits it to S2 for asynchronous processing.

        Args:
            session: Current session.
            output: MicroTurnOutput containing delegation info.
        """
        if output.context_for_delegation is None:
            return

        # Extract query from the actual interaction context
        query_text = "Analyze the recent conversation context."
        if output.context_for_delegation:
            ctx = output.context_for_delegation
            if ctx.get("user_has_spoken"):
                query_text = "The user has been speaking. Analyze their intent."
            query_text += (
                f" (silence: {output.silence_duration_ms:.0f}ms, "
                f"cells: {ctx.get('num_context_cells', 0)})"
            )

        # Build context package
        ctx_package = None
        if self.context_packager:
            recent_cells = self.interaction_model.temporal_grid.get_recent_cells()
            ctx_package = self.context_packager.build_package(
                query=query_text,
                recent_cells=recent_cells,
                silence_duration_ms=output.silence_duration_ms,
            )

        # Create background task with versioning metadata
        task = BackgroundTask(
            task_id=f"task_{session.session_id}_{session.metrics.total_delegations}",
            task_type=BackgroundTaskType.MIXED,
            query=query_text,
            context={
                "session_id": session.session_id,
                "micro_turn_id": session.metrics.total_micro_turns,
                **(self.context_packager.to_dict(ctx_package) if ctx_package else {}),
            },
        )

        # Submit to background model
        self.background_model.submit(task)
        self._task_sessions[task.task_id] = session.session_id
        session.register_delegation(task.task_id)

    def _check_background_results(
        self,
    ) -> None:
        """Check for completed background tasks.

        Streams results from the Background Model into the Bridge
        for injection into S1.
        """
        if not self.background_model:
            return

        # Stream available results into the bridge
        for result in self.background_model.stream_results(timeout=0.0):
            owner_id = result.session_id or self._task_sessions.get(result.task_id)
            if owner_id is None:
                continue
            owner = self._sessions.get(owner_id)
            injector = self._session_injectors.get(owner_id)
            if owner is None or injector is None:
                self._task_sessions.pop(result.task_id, None)
                continue

            injector.receive_result(
                result=result,
                stream_id=result.task_id,
                priority=InjectionPriority.NORMAL,
            )
            if not result.partial:
                owner.complete_background_task(result.task_id)
                self._task_sessions.pop(result.task_id, None)

    def _on_bridge_check(self, tick_id: int) -> None:
        """Scheduler callback: check bridge for pending injections.

        This is called once per tick (200ms) by the scheduler.
        """
        # This is a lightweight check that runs within the scheduler.
        # Heavy processing happens in process_micro_turn.
        pass

    def stream_session(
        self,
        session_id: str,
        audio_stream: Generator,
    ) -> Generator[MicroTurnOutput, None, None]:
        """Generator-based streaming interface.

        Yields MicroTurnOutput for each micro-turn as audio arrives.

        Args:
            session_id: Session identifier.
            audio_stream: Generator yielding audio chunks.

        Yields:
            MicroTurnOutput for each processed micro-turn.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")

        session.start()

        for audio_chunk in audio_stream:
            if not session.is_active:
                break

            output = self.process_micro_turn(
                session_id=session_id,
                audio_chunk=audio_chunk,
            )

            if output is not None:
                yield output

        session.end()

    def get_session_summary(self, session_id: str) -> Optional[Dict]:
        """Get a summary of a session's state and metrics."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return session.summary

    @property
    def active_sessions(self) -> int:
        """Number of active sessions."""
        return sum(
            1 for s in self._sessions.values()
            if s.is_active
        )

    @property
    def bridge_stats(self) -> Optional[Dict]:
        """Get bridge injection statistics."""
        if not self.bridge_injector:
            return None
        totals = {
            "total_injected": 0,
            "total_cancelled": 0,
            "total_expired": 0,
            "pending": 0,
            "active_streams": 0,
        }
        for injector in self._session_injectors.values():
            for key, value in injector.stats.items():
                totals[key] += value
        return totals
