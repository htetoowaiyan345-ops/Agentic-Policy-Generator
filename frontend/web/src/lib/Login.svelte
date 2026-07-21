<script lang="ts">
  import { login } from './api';
  import { setCurrentUser } from './stores';
  import { setToken } from './auth';

  let username = $state('');
  let password = $state('');
  let submitting = $state(false);
  let error = $state<string | null>(null);

  async function onSubmit(e: Event): Promise<void> {
    e.preventDefault();
    if (submitting) return;
    if (!username.trim() || !password) {
      error = 'Please enter your username and password.';
      return;
    }
    submitting = true;
    error = null;
    try {
      const result = await login(username.trim(), password);
      // login() already stores the token via setToken, but be explicit:
      setToken(result.token);
      setCurrentUser(result.user);
      // Reset the form so a re-login starts clean.
      password = '';
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      error = msg;
    } finally {
      submitting = false;
    }
  }
</script>

<div class="login-wrap">
  <form class="login-card" onsubmit={onSubmit}>
    <div class="login-eyebrow mono-tag">Stage 01</div>
    <h1 class="login-title">Sign in</h1>
    <p class="login-sub">Use one of the 3 seed accounts to continue.</p>

    <label class="login-label" for="login-username">Username</label>
    <input
      id="login-username"
      class="login-input"
      type="text"
      autocomplete="username"
      bind:value={username}
      disabled={submitting}
      placeholder="admin / user1 / user2"
    />

    <label class="login-label" for="login-password">Password</label>
    <input
      id="login-password"
      class="login-input"
      type="password"
      autocomplete="current-password"
      bind:value={password}
      disabled={submitting}
      placeholder="admin123 (admin) or user123 (user1/user2)"
    />

    {#if error}
      <div class="login-error" role="alert">{error}</div>
    {/if}

    <button
      type="submit"
      class="pill-btn login-submit"
      disabled={submitting}
    >
      {submitting ? 'Signing in…' : 'Sign in'}
    </button>

    <div class="login-hint mono-label">
      admin / admin123 · user1 / user123 · user2 / user123
    </div>
  </form>
</div>

<style>
  .login-wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 70vh;
    padding: 2rem;
  }
  .login-card {
    width: 100%;
    max-width: 420px;
    background: var(--cream, #f4f1ec);
    border: 1px solid #111;
    padding: 2.5rem 2rem 2rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .login-eyebrow {
    font-size: 0.75rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #555;
  }
  .login-title {
    font-family: 'Source Serif 4', Georgia, serif;
    font-size: 2rem;
    font-weight: 400;
    margin: 0 0 0.25rem;
    line-height: 1.1;
  }
  .login-sub {
    font-size: 0.875rem;
    color: #444;
    margin: 0 0 1.5rem;
  }
  .login-label {
    font-family: var(--mono, monospace);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #333;
    margin-top: 0.5rem;
  }
  .login-input {
    font-family: var(--mono, monospace);
    font-size: 0.9rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid #111;
    background: #fff;
    color: #111;
  }
  .login-input:focus {
    outline: 2px solid #111;
    outline-offset: 1px;
  }
  .login-error {
    margin-top: 0.75rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid #b00;
    background: #fee;
    color: #900;
    font-family: var(--mono, monospace);
    font-size: 0.8rem;
  }
  .login-submit {
    margin-top: 1.25rem;
    align-self: flex-start;
  }
  .login-hint {
    margin-top: 1rem;
    font-size: 0.7rem;
    color: #777;
    text-align: center;
  }
</style>
