"""Buffer adjacent Responses API deltas before forwarding completion events."""


class ResponseDeltaBuffer:
    def __init__(
        self,
        *,
        event_emitter,
        save_response_delta,
        get_response_delta_key,
        full_output,
        continuing,
        delta_chunk_size,
        prior_output,
        append_to_text_field,
    ):
        self.event_emitter = event_emitter
        self.save_response_delta = save_response_delta
        self.get_response_delta_key = get_response_delta_key
        self.full_output = full_output
        self.continuing = continuing
        self.delta_chunk_size = delta_chunk_size
        self.prior_output = prior_output
        self.append_to_text_field = append_to_text_field
        self.delta_count = 0
        self.last_delta_data = None
        self.last_delta_type = None
        self.last_delta_key = None

    def with_prior_output(self, response_data):
        if self.prior_output and isinstance(response_data.get('output_index'), int):
            return {
                **response_data,
                'output_index': response_data['output_index'] + len(self.prior_output),
            }
        if self.prior_output and isinstance(response_data.get('response'), dict):
            # response.output is this round only; response.completed must not replace earlier rounds.
            return {
                **response_data,
                'response': {key: value for key, value in response_data['response'].items() if key != 'output'},
            }
        return response_data

    async def flush_pending(self, threshold=0):
        if self.delta_count >= threshold and self.last_delta_data:
            await self.event_emitter(
                {
                    'type': 'chat:completion' if self.continuing else 'response:completion',
                    'data': {'output': self.full_output(), 'type': self.last_delta_data.get('type')}
                    if self.continuing
                    else self.last_delta_data,
                }
            )
            self.delta_count = 0
            self.last_delta_data = None
            self.last_delta_type = None
            self.last_delta_key = None

    async def queue(self, delta_data, delta_type):
        delta_data = self.with_prior_output(delta_data)
        await self.save_response_delta(delta_data)
        delta_key = self.get_response_delta_key(delta_data)
        if (
            self.last_delta_data
            and self.last_delta_key == delta_key
            and isinstance(self.last_delta_data.get('delta'), str)
            and isinstance(delta_data.get('delta'), str)
        ):
            self.append_to_text_field(self.last_delta_data, 'delta', delta_data['delta'])
            self.delta_count += 1
        else:
            if self.last_delta_data and (self.last_delta_type != delta_type or self.last_delta_key != delta_key):
                await self.flush_pending()

            self.delta_count += 1
            self.last_delta_data = delta_data
            self.last_delta_type = delta_type
            self.last_delta_key = delta_key

        if self.delta_count >= self.delta_chunk_size:
            await self.flush_pending(self.delta_chunk_size)
