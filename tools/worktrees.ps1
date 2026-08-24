<#
Worktrees for a line of work, and a policy that keeps them from piling up.

A branch that outlives the work on it is a merge conflict with a delay fuse.
This repo's history is linear and its default is trunk: small changes land on
`main` directly. A worktree is for work that wants a few commits of its own --
one per *category* of work, never one per task -- and the rules are enforced
here rather than remembered:

  * ONE BRANCH PER CATEGORY.       `<prefix>/<category>`, reused for every task
                                   in that category. Not one per task, not one
                                   per day, not one per agent session.
  * A WIP LIMIT.                   -Limit open worktrees at a time (default 2).
                                   `open` refuses the next one and names what
                                   to land first. A limit you can exceed is a
                                   suggestion.
  * LAND, DO NOT LEAVE.            `land` tests, rebases onto main, fast-
                                   forwards, deletes the branch AND removes the
                                   worktree in one command -- so finishing is
                                   cheaper than not finishing.
  * MERGED MEANS GONE.             `prune` reaps every worktree whose branch is
                                   already in main, and reports the rest by age.

  .\tools\worktrees.ps1 list
  .\tools\worktrees.ps1 open  -Name scene          # -> interior/scene
  .\tools\worktrees.ps1 land  -Name scene
  .\tools\worktrees.ps1 prune                      # -Force to actually remove
  .\tools\worktrees.ps1 policy

Full rationale: docs/branching.md
#>
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('list','open','land','prune','policy')]
  [string]$Cmd,
  [string]$Name,
  [string]$Prefix = "interior",
  [string]$Trunk  = "main",
  [int]$Limit     = 2,
  [int]$StaleDays = 3,
  [switch]$Force,
  [switch]$NoTest
)

$ErrorActionPreference = "Stop"

function Git-Or-Throw([string[]]$gitArgs) {
  # No `2>&1`. Windows PowerShell wraps a native command's stderr in
  # ErrorRecords, and `git worktree add` writes its ordinary progress there --
  # so with $ErrorActionPreference = "Stop" a *successful* command throws
  # NativeCommandError. The exit code is the only thing worth reading.
  $out = & git @gitArgs
  if ($LASTEXITCODE -ne 0) { throw "git $($gitArgs -join ' ') failed (exit $LASTEXITCODE)" }
  return $out
}

# The MAIN worktree's root, not the current one. `--show-toplevel` answers
# "which checkout am I standing in", so running this from inside a worktree
# made it look for .claude/worktrees *under that worktree* and report no
# worktrees at all. `--git-common-dir` is shared by every worktree of a repo,
# and its parent is the main checkout.
$common = (& git rev-parse --path-format=absolute --git-common-dir)
if ($LASTEXITCODE -ne 0) { throw "not a git repository" }
$root = (Resolve-Path (Split-Path $common.Trim() -Parent)).Path
# Worktrees live under .claude/worktrees, which is already gitignored -- so a
# worktree never shows up as untracked junk in the parent's status.
$treeRoot = Join-Path $root ".claude/worktrees"

function Branch-Of([string]$category) { "$Prefix/$category" }
function Dir-Of([string]$category)    { Join-Path $treeRoot "$Prefix/$category" }

function Get-Worktrees {
  # `git worktree list --porcelain` emits a blank-line separated record per
  # worktree. Parsed rather than the human format, whose columns move.
  $records = @()
  $cur = $null
  foreach ($line in (& git worktree list --porcelain)) {
    if ($line -match '^worktree (.+)$') {
      if ($cur) { $records += $cur }
      $cur = [pscustomobject]@{ Path = $Matches[1]; Branch = ""; Head = "" }
    } elseif ($line -match '^HEAD (.+)$') {
      $cur.Head = $Matches[1]
    } elseif ($line -match '^branch refs/heads/(.+)$') {
      $cur.Branch = $Matches[1]
    }
  }
  if ($cur) { $records += $cur }

  $mainPath = (Resolve-Path $root).Path
  foreach ($r in $records) {
    $isMain = ((Resolve-Path $r.Path).Path -eq $mainPath)
    $ahead = 0; $behind = 0; $merged = $false; $last = $null; $dirty = 0
    if ($r.Branch -and -not $isMain) {
      $counts = (& git rev-list --left-right --count "$Trunk...$($r.Branch)")
      if ($LASTEXITCODE -eq 0 -and $counts) {
        $parts = ($counts -split '\s+')
        $behind = [int]$parts[0]   # commits on trunk the branch does not have
        $ahead  = [int]$parts[1]   # commits on the branch trunk does not have
      }
      $merged = ($ahead -eq 0)
      $last = (& git log -1 --format=%ct $r.Branch)
      $status = (& git -C $r.Path status --porcelain)
      if ($status) { $dirty = @($status).Count }
    }
    Add-Member -InputObject $r -NotePropertyName IsMain  -NotePropertyValue $isMain
    Add-Member -InputObject $r -NotePropertyName Ahead   -NotePropertyValue $ahead
    Add-Member -InputObject $r -NotePropertyName Behind  -NotePropertyValue $behind
    Add-Member -InputObject $r -NotePropertyName Merged  -NotePropertyValue $merged
    Add-Member -InputObject $r -NotePropertyName Dirty   -NotePropertyValue $dirty
    $age = ""
    if ($last) {
      $days = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::FromUnixTimeSeconds([int64]$last)).TotalDays
      $age = [math]::Round($days, 1)
    }
    Add-Member -InputObject $r -NotePropertyName AgeDays -NotePropertyValue $age
  }
  return $records
}

function Show-List {
  $wts = Get-Worktrees
  $open = @($wts | Where-Object { -not $_.IsMain })
  "{0,-28} {1,-44} {2,6} {3,7} {4,6} {5}" -f "BRANCH","PATH","AHEAD","BEHIND","DIRTY","AGE(d)"
  foreach ($w in $wts) {
    $label = $w.Branch
    if (-not $label) { $label = "(detached)" }
    if ($w.IsMain) { $label = "$label [trunk]" }
    $rel = $w.Path.Replace("\", "/").Replace($root.Replace("\", "/"), ".")
    "{0,-28} {1,-44} {2,6} {3,7} {4,6} {5}" -f $label, $rel, $w.Ahead, $w.Behind, $w.Dirty, $w.AgeDays
  }
  ""
  "$($open.Count) open worktree(s) of $Limit allowed."
  $reapable = @($open | Where-Object { $_.Merged })
  if ($reapable.Count -gt 0) {
    "$($reapable.Count) already merged into $Trunk -- run: .\tools\worktrees.ps1 prune -Force"
  }
  $stale = @($open | Where-Object { -not $_.Merged -and $_.AgeDays -ne "" -and [double]$_.AgeDays -gt $StaleDays })
  foreach ($s in $stale) {
    "STALE: $($s.Branch) has unmerged work $($s.AgeDays) days old. Land it or delete it."
  }
}

switch ($Cmd) {

  'policy' {
    @"
Branch policy (docs/branching.md is the long form)

  1. Trunk by default. A change that is one commit goes on $Trunk. A worktree
     is for work that wants several, or that has to be interrupted.
  2. One branch per category: $Prefix/<category>. Reused, not re-cut. Two
     tasks in the same category share the branch and land together.
  3. At most $Limit open at once. `open` refuses the next one.
  4. `land` is the only way out: it tests, rebases onto $Trunk, fast-forwards,
     deletes the branch and removes the worktree. One command, so finishing is
     cheaper than leaving it open.
  5. Anything merged is deleted the moment it is merged (`prune`). Anything
     unmerged older than $StaleDays days is reported by `list` until it is
     landed or abandoned.
  6. Nothing is force-deleted with unmerged commits without -Force. The policy
     reduces branches; it does not lose work.
"@
  }

  'list' { Show-List }

  'open' {
    if (-not $Name) { throw "open needs -Name <category>, e.g. -Name scene" }
    $branch = Branch-Of $Name
    $dir    = Dir-Of $Name

    $wts  = Get-Worktrees
    $open = @($wts | Where-Object { -not $_.IsMain })
    $already = @($open | Where-Object { $_.Branch -eq $branch })
    if ($already.Count -gt 0) {
      "already open: $branch at $($already[0].Path)"
      break
    }
    if ($open.Count -ge $Limit) {
      $names = ($open | ForEach-Object { $_.Branch }) -join ", "
      throw ("WIP limit reached: $($open.Count) open ($names) and -Limit is $Limit. " +
             "Land one first:  .\tools\worktrees.ps1 land -Name <category>")
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $dir -Parent) | Out-Null
    & git show-ref --verify --quiet "refs/heads/$branch"
    $exists = ($LASTEXITCODE -eq 0)
    if ($exists) {
      Git-Or-Throw @('worktree','add',$dir,$branch) | Out-Null
      "reopened $branch at $dir"
    } else {
      Git-Or-Throw @('worktree','add','-b',$branch,$dir,$Trunk) | Out-Null
      "opened $branch at $dir (from $Trunk)"
    }
    "  cd $dir"
  }

  'land' {
    if (-not $Name) { throw "land needs -Name <category>" }
    $branch = Branch-Of $Name
    $dir    = Dir-Of $Name
    if (-not (Test-Path $dir)) { throw "no worktree at $dir" }

    $dirty = (& git -C $dir status --porcelain)
    if ($dirty) {
      throw "$branch has uncommitted changes; commit or discard them first: $dirty"
    }

    $counts = (& git rev-list --left-right --count "$Trunk...$branch")
    $parts  = ($counts -split '\s+')
    $ahead  = [int]$parts[1]
    if ($ahead -eq 0) {
      "$branch has nothing $Trunk does not already have -- removing it."
    } else {
      if (-not $NoTest) {
        # From *inside* the worktree. citysmith is not pip-installed, so
        # `import citysmith` resolves against the current directory: running
        # pytest from the parent with the worktree as an argument collects the
        # branch's tests and runs them against main's code, which passes for
        # exactly the wrong reason.
        "running tests in $dir ..."
        Push-Location $dir
        try {
          & python -m pytest -q 2>&1 | Select-Object -Last 12
          $failed = ($LASTEXITCODE -ne 0)
        } finally { Pop-Location }
        if ($failed) { throw "tests failed in $branch -- not landing it" }
      }
      # Rebase rather than merge: this repo's history is linear, and a
      # fast-forward is the only merge that keeps it that way.
      if ([int]$parts[0] -gt 0) {
        "rebasing $branch onto $Trunk ($($parts[0]) commit(s) behind) ..."
        & git -C $dir rebase $Trunk
        if ($LASTEXITCODE -ne 0) {
          throw "rebase of $branch onto $Trunk stopped; resolve it in $dir, then land again"
        }
      }
      Git-Or-Throw @('merge','--ff-only',$branch) | Out-Null
      "merged $branch into $Trunk (fast-forward)"
    }

    Git-Or-Throw @('worktree','remove',$dir) | Out-Null
    Git-Or-Throw @('branch','-d',$branch) | Out-Null
    "landed and removed $branch"
    Show-List
  }

  'prune' {
    $wts  = Get-Worktrees
    $open = @($wts | Where-Object { -not $_.IsMain })
    $n = 0
    foreach ($w in $open) {
      if (-not $w.Branch) { continue }
      if (-not $w.Merged) {
        "keep   $($w.Branch)  -- $($w.Ahead) unmerged commit(s), $($w.AgeDays) days old"
        continue
      }
      if ($w.Dirty -gt 0 -and -not $Force) {
        "keep   $($w.Branch)  -- merged, but $($w.Dirty) uncommitted change(s); -Force to discard"
        continue
      }
      if (-not $Force) {
        "would remove $($w.Branch) at $($w.Path)  (merged into $Trunk)"
        continue
      }
      & git worktree remove --force $w.Path
      if ($LASTEXITCODE -ne 0) { "  could not remove $($w.Path)"; continue }
      & git branch -d $w.Branch | Out-Null
      "removed $($w.Branch)"
      $n++
    }
    if (-not $Force) { "(dry run -- add -Force to remove)" } else { "pruned $n worktree(s)" }
    ""
    Show-List
  }
}
