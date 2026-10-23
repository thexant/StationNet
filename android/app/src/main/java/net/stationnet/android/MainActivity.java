package net.stationnet.android;

import android.app.Activity;
import android.Manifest;
import android.content.pm.PackageManager;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.net.Inet4Address;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class MainActivity extends Activity {
    private TextView statusView;
    private TextView addressView;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refreshTask = new Runnable() {
        @Override public void run() { refresh(); handler.postDelayed(this, 2000); }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        page.setPadding(pad, pad, pad, pad);
        statusView = new TextView(this);
        statusView.setTextSize(22);
        page.addView(statusView);
        addressView = new TextView(this);
        addressView.setTextSize(16);
        addressView.setTextIsSelectable(true);
        page.addView(addressView);
        addButton(page, "Start station", v -> {
            Intent start = new Intent(this, StationService.class).setAction(StationService.START);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(start); else startService(start);
            statusView.setText("Starting");
        });
        addButton(page, "Stop station", v -> startService(
            new Intent(this, StationService.class).setAction(StationService.STOP)));
        addButton(page, "Open station on this phone", v -> startActivity(
            new Intent(Intent.ACTION_VIEW, Uri.parse("http://127.0.0.1:8080/"))));
        addButton(page, "Hotspot settings", v -> {
            try { startActivity(new Intent("android.settings.TETHER_SETTINGS")); }
            catch (Exception ignored) { startActivity(new Intent(Settings.ACTION_WIRELESS_SETTINGS)); }
        });
        TextView help = new TextView(this);
        help.setText("Connect another device to the same Wi-Fi or this phone's hotspot, then open one of the addresses above. No mobile data is needed. Keep the station notification active while hosting.");
        page.addView(help);
        setContentView(page);
    }

    private void addButton(LinearLayout page, String label, View.OnClickListener action) {
        Button button = new Button(this);
        button.setText(label);
        button.setOnClickListener(action);
        page.addView(button);
    }

    private void refresh() {
        statusView.setText("Station: " + StationService.status);
        List<String> addresses = new ArrayList<>();
        try {
            for (NetworkInterface network : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!network.isUp() || network.isLoopback()) continue;
                for (java.net.InetAddress address : Collections.list(network.getInetAddresses())) {
                    if (address instanceof Inet4Address && !address.isLoopbackAddress())
                        addresses.add("http://" + address.getHostAddress() + ":8080");
                }
            }
        } catch (Exception ignored) { }
        addressView.setText("On this phone: http://127.0.0.1:8080\nNearby devices:\n" +
            (addresses.isEmpty() ? "Enable Wi-Fi or hotspot to show an address" : String.join("\n", addresses)));
    }

    @Override protected void onResume() { super.onResume(); handler.post(refreshTask); }
    @Override protected void onPause() { handler.removeCallbacks(refreshTask); super.onPause(); }
}
