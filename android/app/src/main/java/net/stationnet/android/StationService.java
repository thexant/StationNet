package net.stationnet.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import com.chaquo.python.Python;

public class StationService extends Service {
    public static final String START = "net.stationnet.android.START";
    public static final String STOP = "net.stationnet.android.STOP";
    public static volatile String status = "Stopped";
    private static final String CHANNEL = "stationnet_host";
    private Thread worker;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) {
            stopStation();
            return START_NOT_STICKY;
        }
        if (worker != null && worker.isAlive()) return START_NOT_STICKY;
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Station hosting", NotificationManager.IMPORTANCE_LOW));
        Notification notification = notification("Starting station");
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, notification);
        }
        PowerManager power = getSystemService(PowerManager.class);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "StationNet:Hosting");
        wakeLock.acquire();
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        if (wifi != null) {
            wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "StationNet:Hosting");
            wifiLock.acquire();
        }
        status = "Starting";
        worker = new Thread(() -> {
            try {
                Python.getInstance().getModule("stationnet_android").callAttr(
                    "run", new java.io.File(getFilesDir(), "station-data").getAbsolutePath());
            } catch (Exception error) {
                status = "Error: " + error.getMessage();
            } finally {
                if (!status.startsWith("Error:")) status = "Stopped";
                releaseLocks();
                stopForeground(STOP_FOREGROUND_REMOVE);
                stopSelf();
            }
        }, "StationNet server");
        worker.start();
        status = "Running on port 8080";
        manager.notify(1, notification("Running on port 8080"));
        return START_NOT_STICKY;
    }

    private Notification notification(String detail) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
            new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop = PendingIntent.getService(this, 1,
            new Intent(this, StationService.class).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("StationNet")
            .setContentText(detail)
            .setContentIntent(open)
            .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_pause, "Stop station", stop).build())
            .setOngoing(true).build();
    }

    private void stopStation() {
        if (worker == null || !worker.isAlive()) {
            status = "Stopped";
            releaseLocks();
            stopSelf();
            return;
        }
        status = "Stopping";
        try { Python.getInstance().getModule("stationnet_android").callAttr("stop"); }
        catch (Exception error) { status = "Error: " + error.getMessage(); }
    }

    private void releaseLocks() {
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
    }

    @Override public void onDestroy() {
        if (worker != null && worker.isAlive()) stopStation();
        super.onDestroy();
    }
}
