<script lang="ts">
	import { getBackendConfig } from '$lib/apis';
	import {
		getAdminConfig,
		getOAuthConfig,
		updateOAuthConfig,
		updateAdminConfig
	} from '$lib/apis/auths';
	import { getGroups } from '$lib/apis/groups';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { config } from '$lib/stores';
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';

	const i18n: any = getContext('i18n');

	let adminConfig: any = null;
	let groups: any[] = [];

	let oauthConfig: any = null;
	$: oauthEditable = oauthConfig?.ENABLE_OAUTH_PERSISTENT_CONFIG ?? true;
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const updateOAuthHandler = async () => {
		if (!oauthConfig || !oauthEditable) return true;
		const res = await updateOAuthConfig(localStorage.token, oauthConfig).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			oauthConfig = res;
		}
		return !!res;
	};

	const updateAdminHandler = async () => {
		if (!adminConfig) return true;
		const res = await updateAdminConfig(localStorage.token, adminConfig).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res?.sessions_revoked) {
			localStorage.removeItem('token');
			window.location.href = '/auth?state=logout&form=signin';
			return false;
		}
		if (res) {
			adminConfig = res;
		}
		return !!res;
	};

	const submitHandler = async () => {
		const oauthSaved = await updateOAuthHandler();
		if (!oauthSaved) return;

		// Save MFA policy last because changing it revokes the current session.
		const adminSaved = await updateAdminHandler();
		if (adminSaved) {
			toast.success($i18n.t('Settings saved successfully!'));
			await config.set(await getBackendConfig());
		}
	};

	onMount(async () => {
		await Promise.all([
			(async () => {
				adminConfig = await getAdminConfig(localStorage.token);
			})(),
			(async () => {
				groups = await getGroups(localStorage.token);
			})(),
			(async () => {
				oauthConfig = await getOAuthConfig(localStorage.token).catch(() => null);
			})()
		]);

	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={submitHandler}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.admin.authentication.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if adminConfig !== null}
			<AdminSettingSection
				title={$i18n.t('settings.admin.authentication.sections.userAccess.title')}
				first
			>
				<AdminSettingRow
					label={$i18n.t('settings.admin.authentication.defaultUserRole.label')}
					description={$i18n.t('settings.admin.authentication.defaultUserRole.description')}
					let:labelId
				>
					<SettingsSelect
						id={`${labelId}-control`}
						bind:value={adminConfig.DEFAULT_USER_ROLE}
						placeholder={$i18n.t('Select a role')}
					>
						<option value="pending">{$i18n.t('pending')}</option>
						<option value="user">{$i18n.t('user')}</option>
						<option value="admin">{$i18n.t('admin')}</option>
					</SettingsSelect>
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.authentication.defaultGroup.label')}
					description={$i18n.t('settings.admin.authentication.defaultGroup.description')}
					let:labelId
				>
					<SettingsSelect
						id={`${labelId}-control`}
						bind:value={adminConfig.DEFAULT_GROUP_ID}
						placeholder={$i18n.t('Select a group')}
					>
						<option value={''}>{$i18n.t('None')}</option>
						{#each groups as group}
							<option value={group.id}>{group.name}</option>
						{/each}
					</SettingsSelect>
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.authentication.loginForm.label')}
					description={$i18n.t('settings.admin.authentication.loginForm.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_LOGIN_FORM} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.authentication.newSignUps.label')}
					description={$i18n.t('settings.admin.authentication.newSignUps.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_SIGNUP} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.authentication.apiKeys.label')}
					description={$i18n.t('settings.admin.authentication.apiKeys.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_API_KEYS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if adminConfig?.ENABLE_API_KEYS}
					<AdminSettingRow
						label={$i18n.t('settings.admin.authentication.apiKeyEndpointRestrictions.label')}
						description={$i18n.t(
							'settings.admin.authentication.apiKeyEndpointRestrictions.description'
						)}
						let:labelId
					>
						<Switch
							bind:state={adminConfig.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS}
							ariaLabelledbyId={labelId}
						/>
					</AdminSettingRow>

					{#if adminConfig?.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS}
						<AdminSettingField
							label={$i18n.t('settings.admin.authentication.allowedEndpoints.label')}
							description={$i18n.t('settings.admin.authentication.allowedEndpoints.description')}
							forId="api-keys-allowed-endpoints"
						>
							<input
								id="api-keys-allowed-endpoints"
								class={inputClass}
								type="text"
								placeholder={`e.g.) /api/v1/messages, /api/v1/channels`}
								bind:value={adminConfig.API_KEYS_ALLOWED_ENDPOINTS}
							/>
							<a
								href="https://docs.openwebui.com/reference/api-endpoints"
								target="_blank"
								class="mt-1 block text-[0.6875rem] text-gray-400 underline hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							>
								{$i18n.t('To learn more about available endpoints, visit our documentation.')}
							</a>
						</AdminSettingField>
					{/if}
				{/if}

				<AdminSettingField
					label={$i18n.t('settings.admin.authentication.jwtExpiration.label')}
					description={$i18n.t('settings.admin.authentication.jwtExpiration.description')}
					forId="jwt-expiration"
				>
					<input
						id="jwt-expiration"
						class={inputClass}
						type="text"
						placeholder={`e.g.) "30m","1h", "10d". `}
						bind:value={adminConfig.JWT_EXPIRES_IN}
					/>

					{#if adminConfig.JWT_EXPIRES_IN === '-1'}
						<a
							href="https://docs.openwebui.com/reference/env-configuration#jwt_expires_in"
							target="_blank"
							class="mt-1 block rounded-lg bg-yellow-500/10 px-2 py-1.5 text-[0.6875rem] text-yellow-700 underline dark:text-yellow-200"
						>
							{$i18n.t('No expiration can pose security risks.')}
						</a>
					{/if}
				</AdminSettingField>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Multi-factor authentication')}>
				<AdminSettingRow let:labelId label={$i18n.t('Require an authenticator for all users')}>
					<Switch
						ariaLabelledbyId={labelId}
						bind:state={adminConfig.ENABLE_MFA}
						ariaLabel={$i18n.t('Require an authenticator for all users')}
					/>
				</AdminSettingRow>
				<p class="-mt-1 text-[0.6875rem] leading-4 text-gray-400 dark:text-gray-600">
					{$i18n.t('Changes sign out all devices. Users enroll on their next sign-in.')}
				</p>
				{#if adminConfig.ENABLE_MFA}
					<AdminSettingRow
						let:labelId
						label={$i18n.t('Allow OAuth sign-in without an authenticator')}
						><Switch
							ariaLabelledbyId={labelId}
							bind:state={adminConfig.MFA_ALLOW_OAUTH_BYPASS}
							ariaLabel={$i18n.t('Allow OAuth sign-in without an authenticator')}
						/></AdminSettingRow
					>
					<AdminSettingRow
						let:labelId
						label={$i18n.t('Allow trusted-header sign-in without an authenticator')}
						><Switch
							ariaLabelledbyId={labelId}
							bind:state={adminConfig.MFA_ALLOW_TRUSTED_HEADER_BYPASS}
							ariaLabel={$i18n.t('Allow trusted-header sign-in without an authenticator')}
						/></AdminSettingRow
					>
				{/if}
			</AdminSettingSection>

			<AdminSettingSection
				title={$i18n.t('settings.admin.authentication.sections.pendingAccounts.title')}
			>
				<AdminSettingRow
					label={$i18n.t('settings.admin.authentication.adminDetails.label')}
					description={$i18n.t('settings.admin.authentication.adminDetails.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.SHOW_ADMIN_DETAILS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if adminConfig.SHOW_ADMIN_DETAILS}
					<AdminSettingField
						label={$i18n.t('settings.admin.authentication.adminContactEmail.label')}
						description={$i18n.t('settings.admin.authentication.adminContactEmail.description')}
						forId="admin-contact-email"
					>
						<input
							id="admin-contact-email"
							class={inputClass}
							type="email"
							placeholder={$i18n.t('Leave empty to use first admin user')}
							bind:value={adminConfig.ADMIN_EMAIL}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingField
					label={$i18n.t('settings.admin.authentication.pendingUserOverlayTitle.label')}
					description={$i18n.t('settings.admin.authentication.pendingUserOverlayTitle.description')}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t(
							'Enter a title for the pending user info overlay. Leave empty for default.'
						)}
						bind:value={adminConfig.PENDING_USER_OVERLAY_TITLE}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('settings.admin.authentication.pendingUserOverlayContent.label')}
					description={$i18n.t(
						'settings.admin.authentication.pendingUserOverlayContent.description'
					)}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t(
							'Enter content for the pending user info overlay. Leave empty for default.'
						)}
						bind:value={adminConfig.PENDING_USER_OVERLAY_CONTENT}
					/>
				</AdminSettingField>
			</AdminSettingSection>
		{/if}

		{#if oauthConfig}
			<AdminSettingSection title={$i18n.t('Google sign-in')}>
				{#if !oauthEditable}
					<div
						class="rounded-lg bg-yellow-500/10 px-2 py-1.5 text-[0.6875rem] text-yellow-700 dark:text-yellow-200"
					>
						{$i18n.t(
							'These settings are read from environment variables and cannot be edited here while {{ENV_VAR}} is disabled.',
							{ ENV_VAR: 'ENABLE_OAUTH_PERSISTENT_CONFIG' }
						)}
					</div>
				{/if}

				<fieldset
					class="flex min-w-0 flex-col gap-2.5 disabled:cursor-not-allowed disabled:opacity-75"
					disabled={!oauthEditable}
				>
					<AdminSettingRow
						label={$i18n.t('settings.admin.authentication.oauthOidc.label')}
						description={$i18n.t('settings.admin.authentication.oauthOidc.description')}
						let:labelId
					>
						<Switch bind:state={oauthConfig.ENABLE_OAUTH} ariaLabelledbyId={labelId} />
					</AdminSettingRow>

					{#if oauthConfig.ENABLE_OAUTH}
						<p class="text-xs text-gray-500 dark:text-gray-400">
							{$i18n.t(
								'Restart all Open WebUI instances to apply changes to the provider connection settings.'
							)}
						</p>
						<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.clientId.label')}
								description={$i18n.t('settings.admin.authentication.clientId.description')}
								forId="oauth-client-id"
							>
								<input
									id="oauth-client-id"
									class={inputClass}
									placeholder={$i18n.t('Enter Client ID')}
									bind:value={oauthConfig.GOOGLE_CLIENT_ID}
								/>
							</AdminSettingField>

							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.clientSecret.label')}
								description={$i18n.t('settings.admin.authentication.clientSecret.description')}
							>
								<SensitiveInput
									variant="settings"
									placeholder={$i18n.t('Enter Client Secret')}
									required={false}
									bind:value={oauthConfig.GOOGLE_CLIENT_SECRET}
								/>
							</AdminSettingField>
						</div>

						<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.redirectUri.label')}
								description={$i18n.t('settings.admin.authentication.redirectUri.description')}
								forId="oauth-redirect-uri"
							>
								<input
									id="oauth-redirect-uri"
									class={inputClass}
									placeholder={$i18n.t('Enter Redirect URI')}
									bind:value={oauthConfig.GOOGLE_REDIRECT_URI}
								/>
							</AdminSettingField>

							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.scopes.label')}
								description={$i18n.t('settings.admin.authentication.scopes.description')}
								forId="oauth-scopes"
							>
								<input
									id="oauth-scopes"
									class={inputClass}
									placeholder="openid email profile"
									bind:value={oauthConfig.GOOGLE_OAUTH_SCOPE}
								/>
							</AdminSettingField>
						</div>

						<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.emailClaim.label')}
								description={$i18n.t('settings.admin.authentication.emailClaim.description')}
								forId="oauth-email-claim"
							>
								<input
									id="oauth-email-claim"
									class={inputClass}
									placeholder="email"
									bind:value={oauthConfig.OAUTH_EMAIL_CLAIM}
								/>
							</AdminSettingField>

							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.usernameClaim.label')}
								description={$i18n.t('settings.admin.authentication.usernameClaim.description')}
								forId="oauth-username-claim"
							>
								<input
									id="oauth-username-claim"
									class={inputClass}
									placeholder="name"
									bind:value={oauthConfig.OAUTH_USERNAME_CLAIM}
								/>
							</AdminSettingField>
						</div>

						<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.pictureClaim.label')}
								description={$i18n.t('settings.admin.authentication.pictureClaim.description')}
								forId="oauth-picture-claim"
							>
								<input
									id="oauth-picture-claim"
									class={inputClass}
									placeholder="picture"
									bind:value={oauthConfig.OAUTH_PICTURE_CLAIM}
								/>
							</AdminSettingField>

							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.subClaim.label')}
								description={$i18n.t('settings.admin.authentication.subClaim.description')}
								forId="oauth-sub-claim"
							>
								<input
									id="oauth-sub-claim"
									class={inputClass}
									placeholder="sub"
									bind:value={oauthConfig.OAUTH_SUB_CLAIM}
								/>
							</AdminSettingField>
						</div>

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.oauthSignup.label')}
							description={$i18n.t('settings.admin.authentication.oauthSignup.description')}
							let:labelId
						>
							<Switch bind:state={oauthConfig.ENABLE_OAUTH_SIGNUP} ariaLabelledbyId={labelId} />
						</AdminSettingRow>

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.mergeAccountsByEmail.label')}
							description={$i18n.t(
								'settings.admin.authentication.mergeAccountsByEmail.description'
							)}
							let:labelId
						>
							<Switch
								bind:state={oauthConfig.OAUTH_MERGE_ACCOUNTS_BY_EMAIL}
								ariaLabelledbyId={labelId}
							/>
						</AdminSettingRow>

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.autoRedirect.label')}
							description={$i18n.t('settings.admin.authentication.autoRedirect.description')}
							let:labelId
						>
							<Switch bind:state={oauthConfig.OAUTH_AUTO_REDIRECT} ariaLabelledbyId={labelId} />
						</AdminSettingRow>

						<AdminSettingField
							label={$i18n.t('settings.admin.authentication.allowedDomains.label')}
							description={$i18n.t('settings.admin.authentication.allowedDomains.description')}
							forId="oauth-allowed-domains"
						>
							<input
								id="oauth-allowed-domains"
								class={inputClass}
								placeholder={$i18n.t('* (all domains)')}
								bind:value={oauthConfig.OAUTH_ALLOWED_DOMAINS}
							/>
						</AdminSettingField>

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.roleMapping.label')}
							description={$i18n.t('settings.admin.authentication.roleMapping.description')}
							let:labelId
						>
							<Switch
								bind:state={oauthConfig.ENABLE_OAUTH_ROLE_MANAGEMENT}
								ariaLabelledbyId={labelId}
							/>
						</AdminSettingRow>

						{#if oauthConfig.ENABLE_OAUTH_ROLE_MANAGEMENT}
							<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
								<AdminSettingField
									label={$i18n.t('settings.admin.authentication.rolesClaim.label')}
									description={$i18n.t('settings.admin.authentication.rolesClaim.description')}
									forId="oauth-roles-claim"
								>
									<input
										id="oauth-roles-claim"
										class={inputClass}
										placeholder="roles"
										bind:value={oauthConfig.OAUTH_ROLES_CLAIM}
									/>
								</AdminSettingField>

								<AdminSettingField
									label={$i18n.t('settings.admin.authentication.adminRoles.label')}
									description={$i18n.t('settings.admin.authentication.adminRoles.description')}
									forId="oauth-admin-roles"
								>
									<input
										id="oauth-admin-roles"
										class={inputClass}
										placeholder="admin"
										bind:value={oauthConfig.OAUTH_ADMIN_ROLES}
									/>
								</AdminSettingField>
							</div>

							<AdminSettingField
								label={$i18n.t('settings.admin.authentication.allowedRoles.label')}
								description={$i18n.t('settings.admin.authentication.allowedRoles.description')}
								forId="oauth-allowed-roles"
							>
								<input
									id="oauth-allowed-roles"
									class={inputClass}
									placeholder="*"
									bind:value={oauthConfig.OAUTH_ALLOWED_ROLES}
								/>
							</AdminSettingField>
						{/if}

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.enableOauthGroupManagement.label')}
							description={$i18n.t(
								'settings.admin.authentication.enableOauthGroupManagement.description'
							)}
							let:labelId
						>
							<Switch
								bind:state={oauthConfig.ENABLE_OAUTH_GROUP_MANAGEMENT}
								ariaLabelledbyId={labelId}
							/>
						</AdminSettingRow>

						{#if oauthConfig.ENABLE_OAUTH_GROUP_MANAGEMENT}
							<AdminSettingRow
								label={$i18n.t('settings.admin.authentication.enableOauthGroupCreation.label')}
								description={$i18n.t(
									'settings.admin.authentication.enableOauthGroupCreation.description'
								)}
								let:labelId
							>
								<Switch
									bind:state={oauthConfig.ENABLE_OAUTH_GROUP_CREATION}
									ariaLabelledbyId={labelId}
								/>
							</AdminSettingRow>

							<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
								<AdminSettingField
									label={$i18n.t('settings.admin.authentication.groupClaim.label')}
									description={$i18n.t('settings.admin.authentication.groupClaim.description')}
									forId="oauth-group-claim"
								>
									<input
										id="oauth-group-claim"
										class={inputClass}
										placeholder="groups"
										bind:value={oauthConfig.OAUTH_GROUP_CLAIM}
									/>
								</AdminSettingField>

								<AdminSettingField
									label={$i18n.t('settings.admin.authentication.blockedGroups.label')}
									description={$i18n.t('settings.admin.authentication.blockedGroups.description')}
									forId="oauth-blocked-groups"
								>
									<input
										id="oauth-blocked-groups"
										class={inputClass}
										placeholder={$i18n.t('Comma-separated group names')}
										bind:value={oauthConfig.OAUTH_BLOCKED_GROUPS}
									/>
								</AdminSettingField>
							</div>
						{/if}

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.updateEmail.label')}
							description={$i18n.t('settings.admin.authentication.updateEmail.description')}
							let:labelId
						>
							<Switch
								bind:state={oauthConfig.OAUTH_UPDATE_EMAIL_ON_LOGIN}
								ariaLabelledbyId={labelId}
							/>
						</AdminSettingRow>

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.updateName.label')}
							description={$i18n.t('settings.admin.authentication.updateName.description')}
							let:labelId
						>
							<Switch
								bind:state={oauthConfig.OAUTH_UPDATE_NAME_ON_LOGIN}
								ariaLabelledbyId={labelId}
							/>
						</AdminSettingRow>

						<AdminSettingRow
							label={$i18n.t('settings.admin.authentication.updatePicture.label')}
							description={$i18n.t('settings.admin.authentication.updatePicture.description')}
							let:labelId
						>
							<Switch
								bind:state={oauthConfig.OAUTH_UPDATE_PICTURE_ON_LOGIN}
								ariaLabelledbyId={labelId}
							/>
						</AdminSettingRow>
					{/if}
				</fieldset>
			</AdminSettingSection>
		{/if}
	</div>

	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
