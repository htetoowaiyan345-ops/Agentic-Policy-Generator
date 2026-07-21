<script lang="ts">
  import { onMount } from 'svelte';
  import { getMe, type User } from './api';
  import { setCurrentUser, currentUser } from './stores';
  import { getToken, clearToken } from './auth';
  import Login from './Login.svelte';

  interface Props {
    children?: import('svelte').Snippet;
  }
  let { children }: Props = $props();

  // `authReady` flips true once we've asked the backend /me so we know
  // whether to render Login or the main app.
  let authReady = $state(false);
  // `bootstrapping` is true while we're silently fetching /me on mount
  // (so the screen doesn't flash a login form when a valid token is
  // present in localStorage).
  let bootstrapping = $state(false);

  let user = $derived($currentUser);

  onMount(async () => {
    const token = getToken();
    if (!token) {
      authReady = true;
      return;
    }
    bootstrapping = true;
    try {
      const me: User | null = await getMe();
      if (me) {
        setCurrentUser(me);
      } else {
        // getMe() already cleared the token on 401
        clearToken();
        setCurrentUser(null);
      }
    } finally {
      bootstrapping = false;
      authReady = true;
    }
  });
</script>

{#if !authReady || bootstrapping}
  <div class="gate-loading mono-label">Loading…</div>
{:else if !user}
  <Login />
{:else}
  {@render children?.()}
{/if}

<style>
  .gate-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 50vh;
    color: #555;
    font-size: 0.85rem;
    letter-spacing: 0.06em;
  }
</style>
