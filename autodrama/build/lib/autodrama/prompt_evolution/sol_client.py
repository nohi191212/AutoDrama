from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel

from autodrama.config import load_settings
from autodrama.providers.json_utils import parse_json_object

from .protocol import EvolutionProposal, JudgeReport


T = TypeVar("T", bound=BaseModel)


def _output_text(payload: dict[str, Any]) -> str:
    value = payload.get("output_text")
    if isinstance(value, str) and value.strip():
        return value
    chunks: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for part in output.get("content") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text") or part.get("content")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    try:
        value = payload["choices"][0]["message"]["content"]
    except Exception as exc:
        raise ValueError(f"response contains no text: {exc}") from exc
    if not isinstance(value, str):
        raise ValueError("response content is not text")
    return value


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ValueError(f"unsupported image type: {path}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class SolVisionClient:
    """Vision-review client supporting Responses and OpenAI Chat Completions transports."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.lk888.ai",
        model: str = "gpt-5.6-sol",
        api_mode: Literal["responses", "chat_completions"] = "chat_completions",
        timeout_seconds: float = 600,
        reasoning_effort: str = "xhigh",
        background: bool = True,
        poll_interval_seconds: float = 5,
        max_poll_seconds: float = 1800,
        max_transport_attempts: int = 4,
        create_timeout_seconds: float = 120,
        max_output_tokens: int = 32768,
        max_schema_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("vision-review API key is empty")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        # AIBOX GPT-5.6 models are exposed only through the OpenAI-compatible
        # Chat Completions endpoint.  Keep this invariant even when an older
        # caller explicitly passes api_mode="responses".
        self.api_mode = self._effective_api_mode(model, api_mode)
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.background = background
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_seconds = max_poll_seconds
        self.max_transport_attempts = max_transport_attempts
        self.create_timeout_seconds = create_timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.max_schema_attempts = max_schema_attempts

    @staticmethod
    def _is_gpt56_model(model: str) -> bool:
        model_name = str(model or "").strip().casefold().rsplit(":", 1)[-1]
        return model_name.startswith("gpt-5.6")

    @classmethod
    def _effective_api_mode(
        cls,
        model: str,
        api_mode: Literal["responses", "chat_completions"],
    ) -> Literal["responses", "chat_completions"]:
        if cls._is_gpt56_model(model):
            return "chat_completions"
        return api_mode

    def _chat_endpoint(self) -> str:
        value = self.base_url.rstrip("/")
        if value.endswith("/v1/chat/completions"):
            return value
        if value.endswith("/v1"):
            return f"{value}/chat/completions"
        return f"{value}/v1/chat/completions"

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        model: str = "gpt-5.6-sol",
        api_mode: Literal["responses", "chat_completions"] | None = None,
    ) -> "SolVisionClient":
        settings = load_settings(config_path)
        provider = settings.providers["aibox"]
        api_key = provider.secret("api_key_env")
        if not api_key:
            raise ValueError("AIBOX_API_KEY is unavailable through config/apikeys")
        chat_models = {"gpt-5.6-luna", "gpt-5.6-sol"}
        selected_mode = api_mode or (
            "chat_completions"
            if model.casefold().startswith("kimi") or model.casefold() in chat_models
            else "responses"
        )
        selected_mode = cls._effective_api_mode(model, selected_mode)
        timeout_seconds = max(600, settings.runtime.request_timeout_seconds)
        return cls(
            api_key=api_key,
            base_url=provider.base_url or "https://api.lk888.ai",
            model=model,
            api_mode=selected_mode,
            timeout_seconds=timeout_seconds,
            create_timeout_seconds=timeout_seconds if selected_mode == "chat_completions" else 120,
        )

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        image_paths: list[str | Path] | None = None,
    ) -> T:
        if self.api_mode == "chat_completions":
            last_error: ValueError | None = None
            current_prompt = prompt
            for attempt in range(1, self.max_schema_attempts + 1):
                try:
                    return await self._generate_json_chat(
                        current_prompt, schema, image_paths=image_paths
                    )
                except ValueError as exc:
                    last_error = exc
                    if attempt >= self.max_schema_attempts:
                        break
                    print(
                        f"Chat schema attempt {attempt}/{self.max_schema_attempts} failed: "
                        f"{type(exc).__name__}; retrying with a compact structure reminder",
                        flush=True,
                    )
                    current_prompt = (
                        prompt
                        + "\n\nSTRUCTURE RETRY: The prior answer did not validate. Keep evidence and defect "
                        "to one short sentence each. Use regions=[] for none/minor severity; for major/critical "
                        "use only Region objects with label,x1,y1,x2,y2. Never put an assessment inside regions. "
                        "Return every requested top-level field, including overall_observation."
                    )
            assert last_error is not None
            raise last_error
        schema_json = schema.model_json_schema()
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Return exactly one JSON object matching this schema. Do not use Markdown.\n"
                    f"{json.dumps(schema_json, ensure_ascii=False)}\n\n{prompt}"
                ),
            }
        ]
        for raw_path in image_paths or []:
            path = Path(raw_path).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            content.append(
                {
                    "type": "input_image",
                    "image_url": _image_data_url(path),
                    "detail": "original",
                }
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": (
                "You are a skeptical image-audit and prompt-optimization engine. "
                "Use only visible evidence, localize defects, reserve high scores, and output valid JSON only."
            ),
            "input": [{"type": "message", "role": "user", "content": content}],
            "reasoning": {"effort": self.reasoning_effort},
            "text": {"format": {"type": "json_object"}},
            "background": self.background,
            "stream": False,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            response = await self._create_with_retries(client, headers, payload)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Sol Responses create HTTP {response.status_code}: {response.text[:2000]}"
                )
            response_payload = response.json()
            if self.background:
                response_payload = await self._poll_background(client, headers, response_payload)
        parsed = parse_json_object(_output_text(response_payload))
        return schema.model_validate(parsed)

    async def _generate_json_chat(
        self,
        prompt: str,
        schema: type[T],
        *,
        image_paths: list[str | Path] | None = None,
    ) -> T:
        schema_json = schema.model_json_schema()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Return exactly one JSON object matching this schema. Do not use Markdown.\n"
                    f"{json.dumps(schema_json, ensure_ascii=False)}\n\n{prompt}"
                ),
            }
        ]
        for raw_path in image_paths or []:
            path = Path(raw_path).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(path), "detail": "high"},
                }
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a skeptical image-audit and prompt-optimization engine. "
                        "Use only visible evidence, localize defects, reserve high scores, and output valid JSON only."
                    ),
                },
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_output_tokens,
            "stream": False,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            response = await self._chat_with_retries(client, headers, payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenAI-compatible chat create HTTP {response.status_code}: {response.text[:2000]}"
            )
        response_payload = response.json()
        try:
            choice = response_payload["choices"][0]
            message = choice["message"]
            output_text = message.get("content")
        except Exception as exc:
            raise ValueError(f"chat response contains no message: {exc}") from exc
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError(
                "chat response contains empty final content: "
                f"finish_reason={choice.get('finish_reason')}, message_keys={sorted(message)}"
            )
        parsed = parse_json_object(output_text)
        return schema.model_validate(parsed)

    async def _chat_with_retries(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_transport_attempts + 1):
            try:
                response = await client.post(
                    self._chat_endpoint(),
                    headers=headers,
                    json=payload,
                    timeout=httpx.Timeout(self.create_timeout_seconds, connect=30),
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout) as exc:
                last_error = exc
            else:
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                last_error = RuntimeError(
                    f"OpenAI-compatible chat HTTP {response.status_code}: {response.text[:1000]}"
                )
            if attempt < self.max_transport_attempts:
                print(
                    f"Chat create attempt {attempt}/{self.max_transport_attempts} failed: "
                    f"{type(last_error).__name__}: {last_error}",
                    flush=True,
                )
                await asyncio.sleep(min(30, 2**attempt))
        assert last_error is not None
        raise RuntimeError(
            f"OpenAI-compatible chat failed after {self.max_transport_attempts} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    async def _create_with_retries(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_transport_attempts + 1):
            try:
                response = await client.post(
                    f"{self.base_url}/v1/responses",
                    headers=headers,
                    json=payload,
                    timeout=httpx.Timeout(self.create_timeout_seconds, connect=30),
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout) as exc:
                last_error = exc
            else:
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                last_error = RuntimeError(
                    f"Sol Responses create HTTP {response.status_code}: {response.text[:1000]}"
                )
            if attempt < self.max_transport_attempts:
                print(
                    f"Sol create attempt {attempt}/{self.max_transport_attempts} failed: "
                    f"{type(last_error).__name__}: {last_error}",
                    flush=True,
                )
                await asyncio.sleep(min(30, 2**attempt))
        assert last_error is not None
        raise RuntimeError(
            f"Sol Responses create failed after {self.max_transport_attempts} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    async def _poll_background(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(payload.get("status") or "")
        if status not in {"queued", "in_progress"}:
            if status and status != "completed":
                raise RuntimeError(f"Sol background response ended with status={status}: {payload.get('error')}")
            return payload
        response_id = str(payload.get("id") or "")
        if not response_id:
            raise ValueError("Sol background response is missing id")
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = loop.time() + self.max_poll_seconds
        next_progress_log = started
        transient_errors = 0
        print(f"Sol background {response_id}: {status}", flush=True)
        while status in {"queued", "in_progress"}:
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"Sol background response {response_id} exceeded {self.max_poll_seconds:.0f}s"
                )
            await asyncio.sleep(self.poll_interval_seconds)
            try:
                response = await client.get(
                    f"{self.base_url}/v1/responses/{response_id}", headers=headers
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout):
                if transient_errors < 5:
                    transient_errors += 1
                    await asyncio.sleep(min(30, self.poll_interval_seconds * (2**transient_errors)))
                    continue
                raise
            if response.status_code in {429, 500, 502, 503, 504} and transient_errors < 5:
                transient_errors += 1
                await asyncio.sleep(min(30, self.poll_interval_seconds * (2**transient_errors)))
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Sol Responses poll HTTP {response.status_code}: {response.text[:2000]}"
                )
            transient_errors = 0
            payload = response.json()
            status = str(payload.get("status") or "")
            if loop.time() >= next_progress_log or status not in {"queued", "in_progress"}:
                print(
                    f"Sol background {response_id}: {status} ({loop.time() - started:.0f}s)",
                    flush=True,
                )
                next_progress_log = loop.time() + 60
        if status != "completed":
            raise RuntimeError(f"Sol background response {response_id} ended with status={status}: {payload.get('error')}")
        return payload

    async def review_pair(
        self,
        *,
        image_id: str,
        image_path: str | Path,
        blind_rubric: str,
        contract_rubric: str,
        scene_contract: dict[str, Any],
    ) -> tuple[JudgeReport, JudgeReport]:
        blind = await self.review_blind(
            image_id=image_id,
            image_path=image_path,
            blind_rubric=blind_rubric,
        )
        contract = await self.review_contract(
            image_id=image_id,
            image_path=image_path,
            contract_rubric=contract_rubric,
            scene_contract=scene_contract,
        )
        return blind, contract

    async def review_blind(
        self,
        *,
        image_id: str,
        image_path: str | Path,
        blind_rubric: str,
    ) -> JudgeReport:
        blind_prompt = (
            f"Image id: {image_id}. This is a BLIND pass. You are not given the source prompt or intended story. "
            "Audit only physical/structural plausibility, cinematic frame design, and visible style finish. "
            "Do not infer charitable explanations for hidden joints or unclear contacts. Keep each evidence and defect "
            "to one concise sentence while returning every listed dimension.\n\n"
            f"RUBRIC:\n{blind_rubric}"
        )
        return await self.generate_json(blind_prompt, JudgeReport, image_paths=[image_path])

    async def review_contract(
        self,
        *,
        image_id: str,
        image_path: str | Path,
        contract_rubric: str,
        scene_contract: dict[str, Any],
    ) -> JudgeReport:
        contract_prompt = (
            f"Image id: {image_id}. This is a CONTRACT pass. Check exact inventory, identity, anatomical side, "
            "screen mapping, prop ownership/state, camera anchor, continuous space, action instant, and evidence visibility. "
            "Any required but hidden or ambiguous fact scores at most 5. Keep each evidence and defect to one concise "
            "sentence while returning every listed dimension.\n\n"
            f"SCENE CONTRACT:\n{json.dumps(scene_contract, ensure_ascii=False, indent=2)}\n\n"
            f"RUBRIC:\n{contract_rubric}"
        )
        return await self.generate_json(
            contract_prompt,
            JudgeReport,
            image_paths=[image_path],
        )

    async def propose_evolution(
        self,
        prompt: str,
        image_paths: list[str | Path],
    ) -> EvolutionProposal:
        return await self.generate_json(prompt, EvolutionProposal, image_paths=image_paths)
