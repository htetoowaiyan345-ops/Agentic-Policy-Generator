<script>
  import '../app.css';
  import AuthGate from '$lib/AuthGate.svelte';
  let { children } = $props();
</script>

<AuthGate>
  {@render children()}
</AuthGate>