import { expect, test, type Page } from '@playwright/test'

const PASSWORD = process.env.E2E_PASSWORD ?? 'a wee bawbee for the bairn'

async function signIn(page: Page, username = 'person_1') {
  await page.goto('/login')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  // Password hashing is deliberately slow (Argon2id); give a loaded machine time.
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Hello', { timeout: 15_000 })
}

async function snap(page: Page, name: string) {
  const project = test.info().project.name
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow, `${name} scrolls sideways at ${project}`).toBe(0)
  await page.screenshot({ path: `screenshots/${name}-${project}.png`, fullPage: true })
}

test('sign-in screen', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await snap(page, 'login')
})

test('a wrong password is refused plainly', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Username').fill('person_1')
  await page.getByLabel('Password').fill('not the password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText(/password don.t match/)
})

test('every screen renders', async ({ page }) => {
  test.slow() // five full-page captures, at 2x on mobile
  await signIn(page)
  await expect(page.getByText('Household')).toBeVisible()
  await snap(page, 'dashboard')

  for (const [path, heading, name] of [
    ['/payday', /payday$/, 'payday'],
    ['/inbox', 'Inbox', 'inbox'],
    ['/budget', 'Maternity', 'budget'],
    ['/transactions', 'Transactions', 'transactions'],
    ['/banks', 'Banks', 'banks'],
    ['/regular', 'Regular payments', 'regular-empty'],
    ['/versions', 'Versions', 'versions'],
    ['/settings', 'Settings', 'settings'],
  ] as const) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(heading)
    await snap(page, name)
  }
})

test('every Plan tab renders', async ({ page }) => {
  test.slow() // six captures, at 2x on mobile
  await signIn(page)
  for (const [tab, text] of [
    ['forecast', 'Paths'],
    ['compare', 'Every line'],
    ['childcare', 'Pay, children, nurseries and work patterns'],
    ['mortgage', /mortgage/i],
    ['take-home', 'Where the rates come from'],
    ['renewals', 'Coming up'],
  ] as const) {
    await page.goto(`/plan/${tab}`)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Plan')
    await expect(page.getByText(text).first()).toBeVisible({ timeout: 15_000 })
    await snap(page, `plan-${tab}`)
  }
})

test('the spreadsheet’s follow-ups wait in the inbox as chores', async ({ page, isMobile }) => {
  await signIn(page)
  await expect(page.getByText(/\d+ chores when you’ve a minute/)).toBeVisible()
  if (isMobile) {
    await page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: /Inbox/ })
      .click()
  } else {
    await page.getByRole('link', { name: 'See chores' }).click()
  }
  await expect(page.getByRole('heading', { name: 'Chores' })).toBeVisible()
  // Chores aren't counted: with no price changes the inbox is clear.
  await expect(page.getByText('Inbox clear. Braw.')).toBeVisible()
  if (isMobile) {
    await page.goto('/more')
    await expect(page.getByRole('link', { name: /Transactions/ })).toBeVisible()
    await snap(page, 'more')
  }
})

test('payday lists both people’s transfers, mine first', async ({ page }) => {
  await signIn(page)
  await page.goto('/payday')
  const sections = page.locator('section.register h2')
  await expect(sections.first()).toHaveText('Your transfers')
  await expect(page.getByText('Person 2’s transfers')).toBeVisible()
  await expect(page.locator('.transfer')).toHaveCount(4)
})

test.describe('changing things', () => {
  test.skip(({ isMobile }) => isMobile, 'Writes run once, on desktop')

  test('tick a payday transfer and untick it', async ({ page }) => {
    await signIn(page)
    await page.goto('/payday')
    const first = page.getByRole('checkbox').first()
    await first.click()
    await expect(first).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByText(/ticked by Person 1/)).toBeVisible()
    await first.click()
    await expect(first).toHaveAttribute('aria-checked', 'false')
  })

  test('import a CSV, then approve one price change and turn one down', async ({ page }) => {
    // A made-up Starling export for Joint Billing: Sky and EE have both gone up. Back to normal
    // sets its own EE price, so approving EE asks what Back to normal should do.
    const csv = [
      'Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes',
      '01/11/2026,SKY DIGITAL,SKY0123456,DIRECT DEBIT,-108.50,4870.00,GENERAL,',
      '01/11/2026,NORTHFIELD B S,MORTGAGE 776512,DIRECT DEBIT,-1192.46,3451.38,GENERAL,',
      '14/11/2026,EE LIMITED,07700900123,DIRECT DEBIT,-47.00,3404.38,GENERAL,',
    ].join('\n')
    await signIn(page)
    await page.goto('/transactions?month=2026-11')
    await page.getByRole('button', { name: 'Import a CSV' }).click()
    await page.getByLabel('Which account is it from?').selectOption('joint_billing')
    await page.getByLabel('Bank export').setInputFiles({
      name: 'joint-november.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(csv),
    })
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('All 3 are new.')).toBeVisible()
    await expect(dialog.getByText('Starling', { exact: true })).toBeVisible()
    await snap(page, 'import-preview')
    await dialog.getByRole('button', { name: 'Import 3 new' }).click()
    await expect(dialog.getByText('3 new transactions imported.')).toBeVisible()
    await snap(page, 'import-summary')
    await dialog.getByRole('link', { name: 'Review price changes' }).click()

    await expect(page.getByRole('heading', { name: 'Price changes' })).toBeVisible()
    await snap(page, 'inbox-price-changes')
    const ee = page.locator('.strip', { hasText: 'Phones (EE)' })
    await expect(ee).toContainText('If you approve: Joint Billing will need')
    await expect(ee).toContainText('Back to normal sets its own amount')
    await ee.getByRole('button', { name: /^Approve from/ }).click()

    // The choice replaces the strip's buttons, with nothing chosen for it.
    const choice = ee.getByRole('group', { name: /Back to normal sets its own/ })
    const save = choice.getByRole('button', { name: 'Save for Back to normal' })
    await expect(save).toBeDisabled()
    await snap(page, 'inbox-what-if-choice')
    // Still waiting after a reload: the choice is kept on the server.
    await page.reload()
    await choice.getByLabel(/Keep its own/).check()
    await save.click()
    await expect(page.getByText('Phones (EE) is up to date everywhere.')).toBeVisible()

    // Turn Sky down, take it back, and turn it down again.
    const sky = page.locator('.strip', { hasText: 'TV (Sky)' })
    await sky.getByRole('button', { name: /^Not a change/ }).click()
    await expect(page.getByText('TV (Sky) stays at £104.75 in the budget.')).toBeVisible()
    await page.getByRole('button', { name: /^Undo/ }).click()
    await sky.getByRole('button', { name: /^Not a change/ }).click()
    await expect(page.locator('.strip')).toHaveCount(0)
    await expect(page.getByText('Inbox clear. Braw.')).toBeVisible()

    await page.goto('/transactions?month=2026-11')
    await expect(page.locator('.txn-list li', { hasText: 'Mortgage (Northfield)' })).toBeVisible()
  })

  test('link the demo bank, bring an account in, sync it and disconnect', async ({ page }) => {
    await signIn(page)
    await page.goto('/banks')
    await page.getByRole('button', { name: 'Link a bank' }).click()
    // The demo bank sends the browser straight back, as a real bank would after sign-in.
    await page
      .getByRole('dialog')
      .getByRole('button', { name: /Demo Bank/ })
      .click()
    const mapping = page.getByRole('dialog', { name: 'What does Demo Bank feed?' })
    await expect(mapping).toBeVisible()
    await mapping.getByLabel(/Demo Current/).selectOption('person_1_monzo')
    await snap(page, 'banks-mapping')
    await mapping.getByRole('button', { name: 'Save and sync' }).click()
    await expect(
      page.getByText(/Demo Bank is linked\. \d+ transactions brought in so far\./),
    ).toBeVisible()

    const strip = page.locator('.bank-strip', { hasText: 'Demo Bank' })
    await expect(strip).toContainText('linked by Person 1')
    await expect(strip).toContainText('Consent ends')
    await expect(strip.locator('.bank-accounts li', { hasText: 'Person 1 Monzo' })).toBeVisible()
    await snap(page, 'banks-linked')
    await strip.getByRole('button', { name: /^Sync now/ }).click()
    // Banks limit how often they can be asked: the button waits, and says until when.
    await expect(strip.getByText(/^Again after \d\d:\d\d$/)).toBeVisible()
    await expect(strip.getByRole('button', { name: /^Sync now/ })).toBeDisabled()

    await strip.getByRole('button', { name: /^Disconnect/ }).click()
    const confirm = page.getByRole('dialog', { name: 'Disconnect Demo Bank?' })
    await confirm.getByLabel(/Also delete the \d+ transactions it brought in/).check()
    await confirm.getByRole('button', { name: /^Disconnect and delete \d+ transactions$/ }).click()
    await expect(
      page.getByText(/Demo Bank disconnected and \d+ transactions deleted\./),
    ).toBeVisible()
    await expect(page.locator('.bank-strip')).toHaveCount(0)
  })

  test('a regular payment the budget lacks, and a bill still paid after a cancel', async ({
    page,
  }) => {
    // Four months of a made-up Netflix, and Domestic and General still taken though the
    // spreadsheet marked it to cancel.
    const rows = [
      '20/07/2026,NETFLIX.COM,NETFLIX.COM,CARD,-10.99,900.00,GENERAL,',
      '20/08/2026,NETFLIX.COM,NETFLIX.COM,CARD,-10.99,880.00,GENERAL,',
      '20/09/2026,NETFLIX.COM,NETFLIX.COM,CARD,-10.99,860.00,GENERAL,',
      '20/10/2026,NETFLIX.COM,NETFLIX.COM,CARD,-10.99,840.00,GENERAL,',
      '10/09/2026,DOMESTIC & GENERAL,PLAN 4471,DIRECT DEBIT,-12.99,850.00,GENERAL,',
      '10/10/2026,DOMESTIC & GENERAL,PLAN 4471,DIRECT DEBIT,-12.99,830.00,GENERAL,',
    ]
    const csv = [
      'Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes',
      ...rows,
    ].join('\n')
    await signIn(page)
    await page.goto('/transactions')
    await page.getByRole('button', { name: 'Import a CSV' }).click()
    await page.getByLabel('Which account is it from?').selectOption('joint_billing')
    await page.getByLabel('Bank export').setInputFiles({
      name: 'joint-regular.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(csv),
    })
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: /^Import \d+ new$/ }).click()
    await expect(dialog.locator('.import-figures')).toContainText('New regular payments')
    await expect(dialog.locator('.import-figures')).toContainText('Payments to check')
    await dialog.getByRole('link', { name: 'Open the inbox' }).click()

    const netflix = page.locator('.strip', { hasText: 'Netflix' })
    await expect(netflix).toContainText('£10.99 every month')
    await snap(page, 'inbox-regular')
    await netflix.getByRole('button', { name: /^Add to the budget/ }).click()
    await netflix.getByLabel('Category').fill('Subscriptions')
    await netflix.getByRole('button', { name: 'Add to the live budget' }).click()
    await expect(page.getByText(/Netflix is in the live budget now/)).toBeVisible()

    const dg = page.locator('.strip', { hasText: 'Domestic and General: still being paid' })
    await expect(dg).toContainText('marked to cancel')
    // Any item can be put away without an answer, and brought back.
    await dg.getByRole('button', { name: /^Dismiss/ }).click()
    await expect(page.getByText(/dismissed\. Nothing in the budget changed/)).toBeVisible()
    await page.locator('.answer').getByRole('button', { name: /^Undo/ }).click()
    await dg.getByRole('button', { name: /^Keep paying it/ }).click()
    await expect(page.getByText(/Domestic and General stays/)).toBeVisible()

    await page.goto('/regular')
    const row = page.locator('.regular-list li', { hasText: 'Netflix' })
    await expect(row).toContainText('Netflix')
    await snap(page, 'regular')
    // Matched to the wrong bill: pick the right one.
    await row.getByRole('button', { name: /^Wrong bill\?/ }).click()
    await expect(row.getByLabel('Which bill is Netflix?')).toBeVisible()
    await snap(page, 'regular-link')
    await row.getByRole('button', { name: 'Back' }).click()
  })

  test('make a forecast path and compare it with the live budget', async ({ page }) => {
    test.slow() // each path is 36 months of budget runs
    await signIn(page)
    // A name of its own, so a rerun against the same database isn't confused by the last one.
    const name = `Back to normal from March ${Date.now().toString(36)}`
    await page.goto('/plan/forecast')
    await page.getByRole('button', { name: 'New path' }).click()
    const sheet = page.getByRole('dialog')
    await sheet.getByLabel('Name').fill(name)
    await sheet.getByRole('button', { name: 'Add a what-if' }).click()
    await sheet.getByLabel('Then').selectOption({ label: 'Back to normal' })
    await sheet.getByLabel('From', { exact: true }).fill('2027-03')
    await sheet.getByRole('button', { name: 'Save the path' }).click()
    await expect(sheet).toBeHidden()
    await page.getByRole('checkbox', { name: new RegExp(name) }).check()
    await page.getByRole('checkbox', { name: /as it is/ }).check()
    const summary = page.locator('.chart-summary')
    await expect(summary).toContainText(name)
    await expect(summary).toContainText('as it is')
    await expect(page.locator('.plan-events').getByText('Back to normal from here')).toBeVisible()
    await snap(page, 'plan-forecast-two-paths')
  })

  test('create a Back to work what-if, change three lines and see the diff', async ({ page }) => {
    test.slow() // three saves and an archive, each refetching the budget
    await signIn(page)
    await page.goto('/versions')
    await page.getByRole('button', { name: 'New what-if' }).click()
    await page.getByLabel('Name').fill('Back to work')
    await page.getByRole('button', { name: 'Create what-if' }).click()
    await page.getByRole('link', { name: 'Back to work' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Back to work')

    const desktop = page.locator('.budget-desktop')
    for (const [line, amount] of [
      ['Groceries', '650'],
      ['Petrol', '150'],
      ['Treat Fund', '40'],
    ]) {
      await desktop.getByRole('button', { name: line, exact: true }).click()
      await page.getByLabel('Amount').fill(amount)
      await page.getByRole('button', { name: 'Save' }).click()
      await expect(page.getByRole('dialog')).toBeHidden()
    }
    await expect(desktop.locator('li[data-origin="modified"]')).toHaveCount(3)
    await expect(desktop.getByText('Changed, was £700.00')).toBeVisible()
    await snap(page, 'budget-what-if')

    // Clean up so the next run starts from the seed again.
    await page.goto('/versions')
    const row = page.locator('.version-list li', { hasText: 'Back to work' })
    await row.getByRole('button', { name: 'Archive' }).click()
    await expect(page.locator('.version-list li', { hasText: 'Back to work' })).toHaveCount(0)
  })
})
