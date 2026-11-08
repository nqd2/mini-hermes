export type ChatInteraction = {
	id: string;
	messageId: string;
	type: string;
	data: any;
	callback: (value: any) => void;
};

/** Owns FIFO interaction state and guarantees each pending callback resolves once. */
export const createChatInteractionQueue = <T extends ChatInteraction>(
	onChange: (interactions: T[]) => void = () => {}
) => {
	let interactions: T[] = [];

	const publish = () => onChange([...interactions]);
	const resolve = (interaction: T, value: unknown) => {
		if (!interactions.includes(interaction)) return false;
		interactions = interactions.filter((item) => item !== interaction);
		publish();
		interaction.callback(value);
		return true;
	};

	return {
		get interactions() {
			return [...interactions];
		},
		get current() {
			return interactions[0];
		},
		enqueue(interaction: T) {
			interactions = [...interactions, interaction];
			publish();
		},
		remove(interactionId: string | null | undefined) {
			if (!interactionId || !interactions.some((item) => item.id === interactionId)) return false;
			interactions = interactions.filter((item) => item.id !== interactionId);
			publish();
			return true;
		},
		resolve,
		resolveForMessage(messageId: string, value: unknown) {
			for (const interaction of [...interactions]) {
				if (interaction.messageId === messageId) resolve(interaction, value);
			}
		},
		resolveAll(value: unknown) {
			for (const interaction of [...interactions]) resolve(interaction, value);
		}
	};
};
