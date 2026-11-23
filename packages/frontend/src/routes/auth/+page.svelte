<script lang="ts">
	import MfaChallengeForm from '$lib/components/auth/MfaChallenge.svelte';
	import { mfaRequest, type MfaChallenge } from '$lib/apis/auths/mfa';
	let mfaChallenge: MfaChallenge | null = null;
	let pendingApproval = false;

	import { toast } from 'svelte-sonner';

	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { getBackendConfig } from '$lib/apis';
	import {
		getSessionUser,
		userSignIn,
		userSignUp,
		updateUserTimezone
	} from '$lib/apis/auths';

	import { WEAI_API_BASE_URL, WEAI_BASE_URL } from '$lib/constants';
	import { WEAI_NAME, config, user, socket } from '$lib/stores';

	import { generateInitialsImage, canvasPixelTest, getUserTimezone } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import OnBoarding from '$lib/components/OnBoarding.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import { redirect } from '@sveltejs/kit';

	const i18n = getContext('i18n');

	let loaded = false;

	let mode = 'signin';

	let form = null;

	let name = '';
	let email = '';
	let password = '';
	let confirmPassword = '';


	let submitting = false;

	const setSessionUser = async (sessionUser, redirectPath: string | null = null) => {
		if (sessionUser) {
			if (sessionUser.next_step === 'pending') {
				pendingApproval = true;
				return;
			}
			if (sessionUser.challenge_token) {
				mfaChallenge = sessionUser;
				password = '';
				confirmPassword = '';
				return;
			}
			toast.success($i18n.t(`You're now logged in.`));
			if (sessionUser.token) {
				localStorage.token = sessionUser.token;
			}
			$socket.emit('user-join', { auth: { token: sessionUser.token } });
			await user.set(sessionUser);
			await config.set(await getBackendConfig());

			// Update user timezone
			const timezone = getUserTimezone();
			if (sessionUser.token && timezone) {
				updateUserTimezone(sessionUser.token, timezone);
			}

			if (!redirectPath) {
				redirectPath = $page.url.searchParams.get('redirect') || '/';
			}

			await goto(redirectPath);
			localStorage.removeItem('redirectPath');
		}
	};

	const signInHandler = async () => {
		const sessionUser = await userSignIn(email, password).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		await setSessionUser(sessionUser);
	};

	const signUpHandler = async () => {
		if ($config?.features?.enable_signup_password_confirmation) {
			if (password !== confirmPassword) {
				toast.error($i18n.t('Passwords do not match.'));
				return;
			}
		}

		const sessionUser = await userSignUp(name, email, password, generateInitialsImage(name)).catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);

		await setSessionUser(sessionUser);
	};

	const submitHandler = async () => {
		if (submitting) {
			return;
		}

		submitting = true;
		try {
			if (mode === 'signin') {
				await signInHandler();
			} else {
				await signUpHandler();
			}
		} finally {
			submitting = false;
		}
	};

	const oauthCallbackHandler = async () => {
		// Get the value of the 'token' cookie
		function getCookie(name) {
			const match = document.cookie.match(
				new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)')
			);
			return match ? decodeURIComponent(match[1]) : null;
		}

		const token = getCookie('token');
		if (!token) {
			return;
		}

		const sessionUser = await getSessionUser(token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (!sessionUser) {
			return;
		}

		localStorage.token = token;
		await setSessionUser(sessionUser, localStorage.getItem('redirectPath') || null);
	};

	let onboarding = false;
	let videoElement: HTMLVideoElement | null = null;
	let playOnInteractionRegistered = false;

	function playBackgroundVideo() {
		if (!videoElement) {
			return;
		}

		videoElement.play().catch(() => {
			if (playOnInteractionRegistered) {
				return;
			}

			playOnInteractionRegistered = true;

			const playOnInteraction = () => {
				videoElement?.play().catch(() => {});
				document.removeEventListener('click', playOnInteraction);
				document.removeEventListener('touchstart', playOnInteraction);
				playOnInteractionRegistered = false;
			};

			document.addEventListener('click', playOnInteraction);
			document.addEventListener('touchstart', playOnInteraction);
		});
	}

	$: if (loaded && !onboarding && videoElement) {
		playBackgroundVideo();
	}

	onMount(async () => {
		const redirectPath = $page.url.searchParams.get('redirect');
		const logout = $page.url.searchParams.get('state') === 'logout';

		if ($user && !logout) {
			goto(redirectPath || '/');
		} else {
			if (redirectPath) {
				localStorage.setItem('redirectPath', redirectPath);
			}
		}

		const error = $page.url.searchParams.get('error');
		if (error) {
			toast.error(error);
		}

		if ($page.url.searchParams.has('mfa')) {
			localStorage.removeItem('token');
			try {
				mfaChallenge = await mfaRequest('challenge');
			} catch (error) {
				toast.error(error instanceof Error ? error.message : String(error));
			}
			loaded = true;
			return;
		}
		if ($page.url.searchParams.has('pending')) {
			pendingApproval = true;
			loaded = true;
			return;
		}
		if (!logout) await oauthCallbackHandler();
		form = $page.url.searchParams.get('form');

		// Auto-redirect to SSO when OAUTH_AUTO_REDIRECT is enabled and the
		// deployment is unambiguously SSO-only (single provider, no login form,
		// no local login). Suppressed after logout, by ?form=, ?error=, onboarding,
		// trusted-header auth, or an existing session/token.
		if ($config?.oauth?.auto_redirect && !logout && !form && !error) {
			const providers = Object.keys($config?.oauth?.providers ?? {});
			if (
				providers.length === 1 &&
				$config?.features?.auth !== false &&
				$config?.features?.enable_login_form === false &&
				!$config?.features?.auth_trusted_header &&
				!$config?.onboarding &&
				!localStorage.token &&
				!document.cookie.split('; ').some((c) => c.startsWith('token='))
			) {
				window.location.href = `${WEAI_BASE_URL}/oauth/${providers[0]}/login`;
				return;
			}
		}

		loaded = true;

		if (($config?.features?.auth_trusted_header ?? false) || $config?.features?.auth === false) {
			await signInHandler();
		} else {
			onboarding = $config?.onboarding ?? false;
		}
	});
</script>

<svelte:head>
	<title>
		{`${$WEAI_NAME}`}
	</title>
</svelte:head>

<OnBoarding
	bind:show={onboarding}
	getStartedHandler={() => {
		onboarding = false;
		mode = 'signup';
	}}
/>

<div class="w-full h-dvh text-white relative overflow-hidden" id="auth-page">
	<video
		bind:this={videoElement}
		class="absolute inset-0 h-full w-full object-cover"
		src="/assets/welcome.mp4"
		autoplay
		muted
		loop
		playsinline
		preload="auto"
		poster="/assets/welcome.webp"
		aria-hidden="true"
	></video>
	<div class="absolute inset-0 bg-black/25"></div>
	<div class="absolute inset-0 bg-linear-to-b from-black/35 via-black/15 to-black/60"></div>

	<div class="w-full absolute top-0 left-0 right-0 h-8 drag-region"></div>

	{#if loaded && !onboarding}
		<div
			class="fixed inset-0 h-dvh overflow-y-auto bg-transparent w-full z-50 text-white"
			id="auth-container"
		>
			<div class="w-full p-6 min-h-full flex flex-col text-center">
				{#if mfaChallenge}
					<div
						class="dark my-auto mx-auto w-full sm:max-w-[400px] shrink-0 rounded-2xl border border-white/15 bg-neutral-950/75 p-7 text-white shadow-[0_24px_60px_-12px_rgba(0,0,0,0.65)] backdrop-blur-2xl sm:p-8"
					>
						<MfaChallengeForm
							challenge={mfaChallenge}
							onComplete={setSessionUser}
							onCancel={() => {
								mfaChallenge = null;
								window.location.href = '/auth?state=logout&form=signin';
							}}
						/>
					</div>
				{:else if pendingApproval}
					<div
						class="dark my-auto mx-auto w-full sm:max-w-[400px] rounded-2xl border border-white/15 bg-neutral-950/75 p-7 text-white shadow-[0_24px_60px_-12px_rgba(0,0,0,0.65)] backdrop-blur-2xl sm:p-8"
					>
						<p class="text-sm text-white/80">
							{$i18n.t('Your account is awaiting administrator approval.')}
						</p>
					</div>
				{:else if ($config?.features.auth_trusted_header ?? false) || $config?.features.auth === false}
					<div
						class="dark my-auto mx-auto w-full sm:max-w-[400px] rounded-2xl border border-white/15 bg-neutral-950/75 p-7 text-white shadow-[0_24px_60px_-12px_rgba(0,0,0,0.65)] backdrop-blur-2xl sm:p-8"
					>
						<div
							class="flex items-center justify-center gap-3 text-lg sm:text-xl text-center font-medium text-white"
						>
							<div>
								{$i18n.t('Signing in to {{WEAI_NAME}}', { WEAI_NAME: $WEAI_NAME })}
							</div>

							<div>
								<Spinner variant="brand" className="h-8 w-auto" />
							</div>
						</div>
					</div>
				{:else}
					<div class="my-auto flex flex-col justify-center items-center">
						<div
							id="auth-login-card"
							class="dark my-auto w-full sm:max-w-[400px] rounded-2xl border border-white/15 bg-neutral-950/75 p-7 text-white shadow-[0_24px_60px_-12px_rgba(0,0,0,0.65)] backdrop-blur-2xl sm:p-8"
						>
							<form
								class="flex flex-col justify-center"
								on:submit={(e) => {
									e.preventDefault();
									submitHandler();
								}}
							>
								<div class="mb-6 flex flex-col items-center">
									<div
										class="mb-4 flex size-11 items-center justify-center rounded-xl bg-white/95 p-1.5 shadow-md ring-1 ring-white/20"
									>
										<img
											id="logo"
											crossorigin="anonymous"
											src="{WEAI_BASE_URL}/static/favicon.png"
											class="size-full object-contain"
											alt={$WEAI_NAME}
										/>
									</div>

									<h1 class="text-xl font-medium tracking-tight text-white sm:text-2xl">
										{#if $config?.onboarding ?? false}
											{$i18n.t(`Get started with {{WEAI_NAME}}`, { WEAI_NAME: $WEAI_NAME })}

										{:else if mode === 'signin'}
											{$i18n.t(`Sign in to {{WEAI_NAME}}`, { WEAI_NAME: $WEAI_NAME })}
										{:else}
											{$i18n.t(`Sign up to {{WEAI_NAME}}`, { WEAI_NAME: $WEAI_NAME })}
										{/if}
									</h1>

									{#if $config?.onboarding ?? false}
										<div class="mt-2 text-xs font-normal leading-relaxed text-white/60">
											ⓘ {$WEAI_NAME}
											{$i18n.t(
												'does not make any external connections, and your data stays securely on your locally hosted server.'
											)}
										</div>
									{/if}
								</div>

								{#if $config?.features.enable_login_form || form}
									<div class="flex flex-col gap-3.5 text-left">
										{#if mode === 'signup'}
											<div>
												<label
													for="name"
													class="mb-1.5 block text-xs font-medium text-white/75"
													>{$i18n.t('Name')}</label
												>
												<input
													bind:value={name}
													type="text"
													id="name"
													class="h-10 w-full rounded-xl border border-white/15 bg-[#18181b]/90 px-3.5 text-sm text-white outline-hidden transition placeholder:text-white/35 focus:border-white/40 focus:bg-[#18181b]"
													autocomplete="name"
													placeholder={$i18n.t('Enter Your Full Name')}
													required
												/>
											</div>
										{/if}

										<div>
											<label
												for="email"
												class="mb-1.5 block text-xs font-medium text-white/75"
												>{$i18n.t('Email')}</label
											>
											<input
												bind:value={email}
												type="email"
												id="email"
												class="h-10 w-full rounded-xl border border-white/15 bg-[#18181b]/90 px-3.5 text-sm text-white outline-hidden transition placeholder:text-white/35 focus:border-white/40 focus:bg-[#18181b]"
												autocomplete="email"
												name="email"
												placeholder={$i18n.t('Enter Your Email')}
												required
											/>
										</div>

										<div>
											<label
												for="password"
												class="mb-1.5 block text-xs font-medium text-white/75"
												>{$i18n.t('Password')}</label
											>
											<SensitiveInput
												bind:value={password}
												type="password"
												id="password"
												outerClassName="flex h-10 w-full items-center overflow-hidden rounded-xl border border-white/15 bg-[#18181b]/90 transition focus-within:border-white/40 focus-within:bg-[#18181b]"
												inputClassName="h-full min-w-0 flex-1 bg-transparent pl-3.5 pr-1 text-sm text-white placeholder:text-white/35 outline-hidden"
												showButtonClassName="flex h-full items-center !px-3 text-white/50 hover:text-white"
												placeholder={$i18n.t('Enter Your Password')}
												autocomplete={mode === 'signup' ? 'new-password' : 'current-password'}
												name="password"
												screenReader={true}
												required
											/>
										</div>

										{#if mode === 'signup' && $config?.features?.enable_signup_password_confirmation}
											<div>
												<label
													for="confirm-password"
													class="mb-1.5 block text-xs font-medium text-white/75"
													>{$i18n.t('Confirm Password')}</label
												>
												<SensitiveInput
													bind:value={confirmPassword}
													type="password"
													id="confirm-password"
													outerClassName="flex h-10 w-full items-center overflow-hidden rounded-xl border border-white/15 bg-[#18181b]/90 transition focus-within:border-white/40 focus-within:bg-[#18181b]"
													inputClassName="h-full min-w-0 flex-1 bg-transparent pl-3.5 pr-1 text-sm text-white placeholder:text-white/35 outline-hidden"
													showButtonClassName="flex h-full items-center !px-3 text-white/50 hover:text-white"
													placeholder={$i18n.t('Confirm Your Password')}
													autocomplete="new-password"
													name="confirm-password"
													required
												/>
											</div>
										{/if}
									</div>
								{/if}
								<div class="mt-6">
									{#if $config?.features.enable_login_form || form}
										<button
											class="flex h-10 w-full items-center justify-center rounded-xl bg-white text-sm font-medium text-neutral-950 shadow-sm transition hover:bg-white/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white disabled:opacity-50"
											type="submit"
											disabled={submitting}
										>
											<span class="self-center">
												{mode === 'signin'
													? $i18n.t('Sign in')
													: ($config?.onboarding ?? false)
														? $i18n.t('Create Admin Account')
														: $i18n.t('Create Account')}
											</span>

											{#if submitting}
												<span class="ml-2 self-center">
													<Spinner className="size-4" />
												</span>
											{/if}
										</button>

										{#if $config?.features.enable_signup && !($config?.onboarding ?? false)}
											<div class="mt-4 text-center text-xs text-white/65">
												{mode === 'signin'
													? $i18n.t("Don't have an account?")
													: $i18n.t('Already have an account?')}

												<button
													class="ml-1 font-medium text-white underline-offset-4 hover:underline"
													type="button"
													on:click={() => {
														if (mode === 'signin') {
															mode = 'signup';
														} else {
															mode = 'signin';
														}
													}}
												>
													{mode === 'signin' ? $i18n.t('Sign up') : $i18n.t('Sign in')}
												</button>
											</div>
										{/if}
									{/if}
								</div>
							</form>

							{#if Object.keys($config?.oauth?.providers ?? {}).length > 0}
								<div class="my-5 flex items-center justify-center w-full gap-3">
									<hr class="h-px flex-1 border-0 bg-white/15" />
									{#if $config?.features.enable_login_form || form}
										<span class="text-xs font-normal text-white/55 bg-transparent"
											>{$i18n.t('or')}</span
										>
									{/if}

									<hr class="h-px flex-1 border-0 bg-white/15" />
								</div>
								<div class="flex flex-col space-y-2">
									{#if $config?.oauth?.providers?.google}
										<button
											type="button"
											class="flex h-10 w-full items-center justify-center rounded-xl border border-white/15 bg-white/[0.06] text-sm font-medium text-white transition hover:bg-white/[0.12]"
											on:click={() => {
												window.location.href = `${WEAI_BASE_URL}/oauth/google/login`;
											}}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 48 48"
												class="size-5 mr-2.5"
												aria-hidden="true"
											>
												<path
													fill="#EA4335"
													d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
												/><path
													fill="#4285F4"
													d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
												/><path
													fill="#FBBC05"
													d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
												/><path
													fill="#34A853"
													d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
												/><path fill="none" d="M0 0h48v48H0z" />
											</svg>
											<span>{$i18n.t('Continue with {{provider}}', { provider: 'Google' })}</span>
										</button>
									{/if}
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	:global(#auth-page input) {
		color-scheme: dark;
	}

	:global(#auth-page input:-webkit-autofill),
	:global(#auth-page input:-webkit-autofill:hover),
	:global(#auth-page input:-webkit-autofill:focus),
	:global(#auth-page input:-webkit-autofill:active) {
		-webkit-text-fill-color: #ffffff !important;
		caret-color: #ffffff;
		-webkit-box-shadow: 0 0 0 1000px #18181b inset !important;
		box-shadow: 0 0 0 1000px #18181b inset !important;
		transition: background-color 9999s ease-out 0s;
	}
</style>
