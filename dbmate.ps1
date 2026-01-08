# Wrapper to run dbmate using .env.local configuration
$envFile = ".env.local"

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -ne "" -and -not $line.StartsWith("#")) {
            $parts = $line.Split("=", 2)
            if ($parts.Length -eq 2) {
                # Simple parsing, stripping quotes might be needed for rigorousness
                $key = $parts[0].Trim()
                $val = $parts[1].Trim()
                
                # Strip quotes if present
                if ($val.StartsWith('"') -and $val.EndsWith('"')) {
                    $val = $val.Substring(1, $val.Length - 2)
                } 
                elseif ($val.StartsWith("'") -and $val.EndsWith("'")) {
                    $val = $val.Substring(1, $val.Length - 2)
                }

                [System.Environment]::SetEnvironmentVariable($key, $val, [System.EnvironmentVariableTarget]::Process)
            }
        }
    }
}

# Ensure DBMATE specific vars are set if not in .env.local
if (-not $env:DBMATE_MIGRATIONS_DIR) {
    $env:DBMATE_MIGRATIONS_DIR = "./database/migrations"
}
if (-not $env:DBMATE_SCHEMA_FILE) {
    $env:DBMATE_SCHEMA_FILE = "./database/schema.sql"
}
# Construct DATABASE_URL if missing but components exist
if (-not $env:DATABASE_URL -and $env:DB_HOST) {
    # Assuming standard project env vars
    $pass = [uri]::EscapeDataString($env:DB_PASSWORD)
    $env:DATABASE_URL = "postgres://$($env:DB_USER):$pass@$($env:DB_HOST):$($env:DB_PORT)/$($env:DB_NAME)?sslmode=disable"
}

# Run Dbmate
$args = $MyInvocation.BoundParameters.Values + $args
& .\dbmate.exe @args
