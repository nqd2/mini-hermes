<script lang="ts">
	export let className: string = 'size-4';
	export let variant: 'spinner' | 'brand' = 'spinner';
	export let size: 'panel' | 'page' = 'panel';
	export let label: string = 'Loading';

	$: brandSizeClass = size === 'page' ? 'h-20 w-auto' : 'h-12 w-auto';
</script>

{#if variant === 'brand'}
	<div class="flex items-center justify-center" role="status" aria-label={label}>
		<img
			src="/static/splash-loading.svg"
			alt=""
			aria-hidden="true"
			class="select-none pointer-events-none {brandSizeClass} dark:drop-shadow-[0_0_12px_rgba(0,150,255,0.3)] {className ===
			'size-4'
				? ''
				: className}"
			draggable="false"
		/>
	</div>
{:else}
	<div class="flex justify-center text-center">
		<svg
			aria-hidden="true"
			class={className}
			viewBox="0 0 24 24"
			fill="currentColor"
			xmlns="http://www.w3.org/2000/svg"
			><style>
				.spinner_ajPY {
					transform-origin: center;
					animation: spinner_AtaB 0.75s infinite linear;
				}
				@keyframes spinner_AtaB {
					100% {
						transform: rotate(360deg);
					}
				}
			</style><path
				d="M12,1A11,11,0,1,0,23,12,11,11,0,0,0,12,1Zm0,19a8,8,0,1,1,8-8A8,8,0,0,1,12,20Z"
				opacity=".25"
			/><path
				d="M10.14,1.16a11,11,0,0,0-9,8.92A1.59,1.59,0,0,0,2.46,12,1.52,1.52,0,0,0,4.11,10.7a8,8,0,0,1,6.66-6.61A1.42,1.42,0,0,0,12,2.69h0A1.57,1.57,0,0,0,10.14,1.16Z"
				class="spinner_ajPY"
			/></svg
		>
	</div>
{/if}
