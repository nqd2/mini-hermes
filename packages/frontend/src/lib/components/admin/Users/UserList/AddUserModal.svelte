<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher } from 'svelte';
	import { onMount, getContext } from 'svelte';
	import { addUser } from '$lib/apis/auths';

	import { WEAI_BASE_URL } from '$lib/constants';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import { generateInitialsImage } from '$lib/utils';
	import XMark from '$lib/components/icons/XMark.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n: any = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;

	let loading = false;
	let tab = '';
	let inputFiles;

	const MAX_MONTHLY_REQUEST_LIMIT = 2_147_483_647;

	let _user = {
		name: '',
		email: '',
		password: '',
		role: 'user',
		tier: 'free' as 'free' | 'plus',
		monthly_request_limit: 1000
	};

	$: if (show) {
		_user = {
			name: '',
			email: '',
			password: '',
			role: 'user',
			tier: 'free' as 'free' | 'plus',
			monthly_request_limit: 1000
		};
	}

	const submitHandler = async () => {
		const stopLoading = () => {
			dispatch('save');
			loading = false;
		};

		if (tab === '') {
			let monthlyRequestLimit: number | null = null;
			if (_user.tier === 'plus') {
				const parsedLimit = Number(_user.monthly_request_limit);
				if (
					_user.monthly_request_limit === null ||
					_user.monthly_request_limit === undefined ||
					!Number.isInteger(parsedLimit) ||
					parsedLimit < 1 ||
					parsedLimit > MAX_MONTHLY_REQUEST_LIMIT
				) {
					toast.error($i18n.t('Enter a positive whole-number monthly request limit'));
					return;
				}
				monthlyRequestLimit = parsedLimit;
			}

			loading = true;

			const res = await addUser(
				localStorage.token,
				_user.name,
				_user.email,
				_user.password,
				_user.role,
				generateInitialsImage(_user.name),
				_user.tier,
				monthlyRequestLimit
			).catch((error) => {
				toast.error(`${error}`);
			});

			if (res) {
				stopLoading();
				show = false;
			}
		} else {
			if (inputFiles) {
				loading = true;

				const file = inputFiles[0];
				const reader = new FileReader();

				reader.onload = async (e) => {
					const csv = String(e.target?.result ?? '');
					const rows = csv.split('\n');

					const validRows: {
						idx: number;
						columns: string[];
						tier: 'free' | 'plus';
						monthly_request_limit: number | null;
					}[] = [];

					for (const [idx, row] of rows.entries()) {
						const columns = row.split(',').map((col) => col.trim());
						console.debug(idx, columns);

						if (idx > 0 && !(columns.length === 1 && columns[0] === '')) {
							const roleValid =
								columns.length >= 4 &&
								columns.length <= 6 &&
								['admin', 'user', 'pending'].includes(columns[3].toLowerCase());

							if (!roleValid) {
								toast.error(`Row ${idx + 1}: invalid format.`);
								continue;
							}

							let tier: 'free' | 'plus' = 'free';
							let monthly_request_limit: number | null = null;
							let rowValid = true;

							if (columns.length >= 5) {
								const rawTier = columns[4].toLowerCase();
								if (rawTier !== 'free' && rawTier !== 'plus') {
									rowValid = false;
								} else {
									tier = rawTier;
									if (columns.length === 5) {
										monthly_request_limit = tier === 'plus' ? 1000 : null;
									} else if (tier === 'plus') {
										const parsed = /^\d+$/.test(columns[5]) ? Number(columns[5]) : NaN;
										if (
											!Number.isInteger(parsed) ||
											parsed < 1 ||
											parsed > MAX_MONTHLY_REQUEST_LIMIT
										) {
											rowValid = false;
										} else {
											monthly_request_limit = parsed;
										}
									} else if (columns[5] !== '') {
										rowValid = false;
									}
								}
							}

							if (rowValid) {
								validRows.push({ idx, columns, tier, monthly_request_limit });
							} else {
								toast.error(`Row ${idx + 1}: invalid format.`);
							}
						}
					}

					let userCount = 0;
					const BATCH_SIZE = 10;

					for (let i = 0; i < validRows.length; i += BATCH_SIZE) {
						const batch = validRows.slice(i, i + BATCH_SIZE);
						const results = await Promise.allSettled(
							batch.map(({ idx, columns, tier, monthly_request_limit }) =>
								addUser(
									localStorage.token,
									columns[0],
									columns[1],
									columns[2],
									columns[3].toLowerCase(),
									generateInitialsImage(columns[0]),
									tier,
									monthly_request_limit
								).catch((error) => {
									toast.error(`Row ${idx + 1}: ${error}`);
									return null;
								})
							)
						);

						for (const result of results) {
							if (result.status === 'fulfilled' && result.value) {
								userCount++;
							}
						}
					}

					toast.success(
						$i18n.t('Successfully imported {{userCount}} users.', { userCount: userCount })
					);
					inputFiles = null;
					const uploadInputElement = document.getElementById('upload-user-csv-input');

					if (uploadInputElement) {
						uploadInputElement.value = null;
					}

					stopLoading();
				};

				reader.readAsText(file, 'utf-8');
			} else {
				toast.error($i18n.t('File not found.'));
			}
		}

		loading = false;
	};
</script>

<Modal ariaLabel={$i18n.t('Add User')} size="sm" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-4 pt-3 pb-1">
			<div class=" text-sm font-medium self-center">{$i18n.t('Add User')}</div>
			<button
				class="self-center rounded-lg py-1 text-gray-500 transition hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 px-0"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-4'} />
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-4 pb-3 md:space-x-4 dark:text-gray-200">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<form
					class="flex flex-col w-full"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div
						class="flex -mt-2 mb-1.5 gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-normal bg-transparent dark:text-gray-200"
					>
						<button
							class="min-w-fit p-1.5 {tab === ''
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
							type="button"
							on:click={() => {
								tab = '';
							}}>{$i18n.t('Form')}</button
						>

						<button
							class="min-w-fit p-1.5 {tab === 'import'
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
							type="button"
							on:click={() => {
								tab = 'import';
							}}>{$i18n.t('CSV Import')}</button
						>
					</div>

					<div class="px-1">
						{#if tab === ''}
							<div class="flex flex-col w-full mb-3">
								<div class="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">{$i18n.t('Role')}</div>

								<div class="flex-1">
									<select
										class="w-full appearance-none capitalize rounded-xl border border-gray-200 bg-gray-50/70 px-3 pr-8 py-1.5 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600 dark:disabled:text-gray-500"
										bind:value={_user.role}
										aria-label={$i18n.t('Role')}
										placeholder={$i18n.t('Enter Your Role')}
										required
									>
										<option class="bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100" value="pending"> {$i18n.t('pending')} </option>
										<option class="bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100" value="user"> {$i18n.t('user')} </option>
										<option class="bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100" value="admin"> {$i18n.t('admin')} </option>
									</select>
								</div>
							</div>

							<div class="flex flex-col w-full mb-3">
								<div class="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">{$i18n.t('Tier')}</div>

								<div class="flex-1">
									<select
										class="w-full appearance-none rounded-xl border border-gray-200 bg-gray-50/70 px-3 pr-8 py-1.5 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600 dark:disabled:text-gray-500"
										bind:value={_user.tier}
										aria-label={$i18n.t('Tier')}
										required
									>
										<option class="bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100" value="free">{$i18n.t('Free')}</option>
										<option class="bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100" value="plus">{$i18n.t('Plus')}</option>
									</select>
								</div>
							</div>

							{#if _user.tier === 'plus'}
								<div class="flex flex-col w-full mb-3">
									<div class="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">
										{$i18n.t('Monthly Request Limit')}
									</div>

									<div class="flex-1">
										<input
											class="w-full rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-1.5 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 disabled:text-gray-500 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600 dark:disabled:text-gray-500"
											type="number"
											min="1"
											max="2147483647"
											step="1"
											bind:value={_user.monthly_request_limit}
											aria-label={$i18n.t('Monthly Request Limit')}
											required
										/>
									</div>
								</div>
							{/if}

							<div class="flex flex-col w-full mb-3">
								<div class="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">{$i18n.t('Name')}</div>

								<div class="flex-1">
									<input
										class="w-full rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-1.5 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 disabled:text-gray-500 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600 dark:disabled:text-gray-500"
										type="text"
										bind:value={_user.name}
										aria-label={$i18n.t('Name')}
										placeholder={$i18n.t('Enter Your Full Name')}
										autocomplete="off"
										required
									/>
								</div>
							</div>

							<hr class="border-gray-100 dark:border-gray-850 my-2.5 w-full" />

							<div class="flex flex-col w-full mb-3">
								<div class="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">{$i18n.t('Email')}</div>

								<div class="flex-1">
									<input
										class="w-full rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-1.5 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 disabled:text-gray-500 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600 dark:disabled:text-gray-500"
										type="email"
										bind:value={_user.email}
										aria-label={$i18n.t('Email')}
										placeholder={$i18n.t('Enter Your Email')}
										required
									/>
								</div>
							</div>

							<div class="flex flex-col w-full">
								<div class="mb-1 text-xs font-medium text-gray-600 dark:text-gray-400">{$i18n.t('Password')}</div>

								<div class="flex-1 rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-1.5 transition focus-within:border-gray-400 dark:border-gray-800 dark:bg-gray-850 dark:focus-within:border-gray-600">
									<SensitiveInput
										class="w-full text-sm bg-transparent disabled:text-gray-500 dark:disabled:text-gray-500 outline-hidden"
										type="password"
										bind:value={_user.password}
										aria-label={$i18n.t('Password')}
										placeholder={$i18n.t('Enter Your Password')}
										autocomplete="off"
										required
									/>
								</div>
							</div>
						{:else if tab === 'import'}
							<div>
								<div class="mb-3 w-full">
									<input
										id="upload-user-csv-input"
										hidden
										bind:files={inputFiles}
										type="file"
										accept=".csv"
									/>

									<button
										class="w-full text-sm font-normal py-3 bg-transparent hover:bg-gray-100 border border-dashed dark:border-gray-850 dark:hover:bg-gray-850 text-center rounded-xl"
										type="button"
										on:click={() => {
											document.getElementById('upload-user-csv-input')?.click();
										}}
									>
										{#if inputFiles}
											{$i18n.t('{{COUNT}} document(s) selected.', { COUNT: inputFiles.length })}
										{:else}
											{$i18n.t('Click here to select a csv file.')}
										{/if}
									</button>
								</div>

								<div class=" text-xs text-gray-500">
									ⓘ {$i18n.t(
										'Ensure your CSV file includes 4 to 6 columns in this order: Name, Email, Password, Role, and optional Tier (free or plus) and Monthly Request Limit.'
									)}
									<a
										class="underline dark:text-gray-200"
										href="{WEAI_BASE_URL}/static/user-import.csv"
									>
										{$i18n.t('Click here to download user import template file.')}
									</a>
								</div>
							</div>
						{/if}
					</div>

					<div class="flex justify-end pt-3 text-sm font-normal">
						<button
							class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white disabled:cursor-not-allowed"
							type="submit"
							disabled={loading}
						>
							{$i18n.t('Save')}

							{#if loading}
								<span class="shrink-0">
									<Spinner className="size-3" />
								</span>
							{/if}
						</button>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>

<style>
	input::-webkit-outer-spin-button,
	input::-webkit-inner-spin-button {
		/* display: none; <- Crashes Chrome on hover */
		-webkit-appearance: none;
		margin: 0; /* <-- Apparently some margin are still there even though it's hidden */
	}
</style>
