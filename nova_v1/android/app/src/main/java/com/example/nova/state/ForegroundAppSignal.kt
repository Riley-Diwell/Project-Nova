package com.example.nova.state

import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.Process

/**
 * PACKAGE_USAGE_STATS signal: package name of the most recently used app (excluding Nova itself).
 * Not a runtime permission - the user grants it via
 * Settings > Apps > Special app access > Usage access.
 * Check [hasPermission] and direct the user to Settings.ACTION_USAGE_ACCESS_SETTINGS if false.
 */
object ForegroundAppSignal {
    fun hasPermission(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.packageName
            )
        } else {
            @Suppress("DEPRECATION")
            appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.packageName
            )
        }
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun currentForegroundApp(context: Context): String? {
        if (!hasPermission(context)) return null
        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val stats = usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, now - 5_000L, now)
        return stats
            ?.filter { it.packageName != context.packageName }
            ?.maxByOrNull { it.lastTimeUsed }
            ?.packageName
    }
}
