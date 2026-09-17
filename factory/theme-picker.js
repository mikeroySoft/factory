(() => {
  'use strict';

  const key = 'factory-theme';
  const root = document.documentElement;
  const defaultTheme = root.dataset.defaultTheme || 'cyberpunk';
  const themes = [
    ...(defaultTheme === 'repository' ? [['repository', 'Repository']] : []),
    ['cyberpunk', 'Cyberpunk'],
    ['gpuflo', 'GPUFlo'],
    ['district', 'District'],
    ['factory', 'Factory'],
    ['rocm', 'ROCm'],
    ['porcelain', 'Porcelain'],
    ['sandstone', 'Sandstone'],
    ['slate', 'Slate'],
    ['forest', 'Forest'],
  ];
  const valid = new Set(themes.map(([value]) => value));

  const read = () => {
    try { return localStorage.getItem(key); }
    catch { return null; }
  };

  const apply = (theme, persist = false) => {
    const value = valid.has(theme) ? theme : defaultTheme;
    if (value === 'repository') delete root.dataset.theme;
    else root.dataset.theme = value;
    if (persist) {
      try { localStorage.setItem(key, value); }
      catch { /* The selection still applies for this page. */ }
    }
    return value;
  };

  const stored = read();
  apply(stored);

  document.addEventListener('DOMContentLoaded', () => {
    const nav = document.querySelector('.factory-nav');
    if (!nav || document.getElementById('factory-theme-picker')) return;

    const select = document.createElement('select');
    select.id = 'factory-theme-picker';
    select.name = 'factory-theme';
    for (const [value, text] of themes) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = text;
      select.append(option);
    }
    select.value = valid.has(stored) ? stored : defaultTheme;
    select.addEventListener('change', () => apply(select.value, true));

    const label = document.createElement('label');
    label.className = 'theme-picker';
    const text = document.createElement('span');
    text.textContent = 'Theme';
    label.append(text, select);
    nav.append(label);
  });
})();
