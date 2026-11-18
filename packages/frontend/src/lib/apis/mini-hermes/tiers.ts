import { WEBUI_API_BASE_URL } from '$lib/constants';

export type Tier = 'free' | 'plus';
export type Plan = { tier: Tier; monthly_request_limit: number | null };
export type Allowance = Plan & { used: number; remaining: number; reset_at: string };
export type Capability = 'text' | 'image' | 'tools' | 'structured_output' | 'responses';
export type PoolCandidate = {
	connection_id: string;
	model_id: string;
	priority: number;
	min_context: number;
	capabilities: Capability[];
	enabled: boolean;
};
export type RoutingConfig = {
	version: number;
	groups: Record<string, PoolCandidate[]>;
	free_user_daily_limit: number;
	free_account_daily_limit: number;
	free_account_minute_limit: number;
};
export type RoutingSettings = {
	config: RoutingConfig;
	connections: { id: string; url: string; enabled: boolean }[];
};

async function tierRequest<T>(token: string, path: string, data?: object): Promise<T> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes${path}`, {
		method: data ? 'PUT' : 'GET',
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: data ? JSON.stringify(data) : undefined
	});
	const result = await response.json();
	if (!response.ok) {
		throw new Error(result.detail?.code || 'Could not complete Mini-Hermes request');
	}
	return result;
}

export function getMyPlan(token: string) {
	return tierRequest<Allowance>(token, '/me/plan');
}

export function getUserPlan(token: string, userId: string) {
	return tierRequest<Plan>(token, `/admin/users/${encodeURIComponent(userId)}/plan`);
}

export function saveUserPlan(token: string, userId: string, plan: Plan) {
	return tierRequest<Plan>(token, `/admin/users/${encodeURIComponent(userId)}/plan`, plan);
}

export function getRouting(token: string) {
	return tierRequest<RoutingSettings>(token, '/admin/model-routing');
}

export function saveRouting(token: string, config: RoutingConfig) {
	return tierRequest<RoutingConfig>(token, '/admin/model-routing', config);
}
