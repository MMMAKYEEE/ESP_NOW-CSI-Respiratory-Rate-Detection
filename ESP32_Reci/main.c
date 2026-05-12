#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <math.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>



#include "nvs_flash.h"
#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_csi_gain_ctrl.h"
#include "esp_timer.h"


//wifi information
#define WIFI_SSID "WiFi-7425-5G"
#define WIFI_PASS "97322115"

#define UDP_DEST_IP   "192.168.1.255"   // UDP广播地址
#define UDP_DEST_PORT 3333

static int s_udp_sock = -1;
static struct sockaddr_in s_udp_dest_addr;


#define WIFI_CHANNEL                36
#define WIFI_BAND_MODE_CFG          WIFI_BAND_MODE_5G_ONLY
#define WIFI_BANDWIDTH_5G_CFG       WIFI_BW_HT20
#define WIFI_PROTOCOL_5G_CFG        (WIFI_PROTOCOL_11A | WIFI_PROTOCOL_11N)

#define ESPNOW_PHYMODE_CFG          WIFI_PHY_MODE_HT20
#define ESPNOW_RATE_CFG             WIFI_PHY_RATE_MCS0_LGI

#define FORCE_GAIN_ENABLE           0
#define CSI_FORCE_LLTF              0
#define GAIN_CONTROL_ENABLE         1


//GroupEvent 标志位，表示连接成功或失败
static EventGroupHandle_t wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1



static const uint8_t CSI_SEND_MAC[6] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};
//static const uint8_t CSI_RECV_MAC[6] = {0x1a, 0x11, 0x00, 0x00, 0x00, 0x00};

static const char *TAG = "csi_recv_5g_ht20";

static wifi_ap_record_t s_ap_records[20];


uint16_t wifi_channel = 0;



/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/

//wifi事件回调函数
void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data)
{
    static int s_retry_num = 0;

    //当wifi成功配置为sta并开启时，会收到WIFI_EVENT_STA_START事件，此时可以调用esp_wifi_connect()函数连接wifi
    if(event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START)
    {
        esp_wifi_connect();
    }
    //当wifi连接成功时，会收到WIFI_EVENT_STA_CONNECTED事件，接收到此事件后，事件任务将自动开始获取 IP 地址
    else if(event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED)
    {
        //什么都不用干，系统自动进入下一步获取ip地址
    }
    //当主动断开wifi，或连接wifi失败后，会收到WIFI_EVENT_STA_DISCONNECTED事件，此时可以根据需要决定是否重新连接wifi
    //这里选择重新尝试连接wifi，最多尝试10次，超过10次则认为连接失败
    else if(event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED)
    {
        if(s_retry_num < 10)
        {
            esp_wifi_connect();
            s_retry_num++;
        }
        else //fail后要把groupEvent标志位打开，表示连接失败
        {
            xEventGroupSetBits(wifi_event_group, WIFI_FAIL_BIT);
        }
    }
    //ip事件回调函数，当wifi成功连接并获取到ip地址时，会收到IP_EVENT_STA_GOT_IP事件，此时可以获取到ip地址等相关信息
    //一旦获取到ip地址，就可以认为wifi连接成功了，此时要把groupEvent标志位打开，表示连接成功
    else if(event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP)
    {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *) event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));

        wifi_ap_record_t ap_info;
        esp_err_t err = esp_wifi_sta_get_ap_info(&ap_info);
        if (err == ESP_OK) 
        {
            wifi_channel = ap_info.primary;   // 复制当前连接 AP 的主信道
            ESP_LOGI(TAG, "connected AP channel = %d", wifi_channel);
        } 
        else 
        {
            ESP_LOGW(TAG, "failed to get AP info, err = %s", esp_err_to_name(err));
        }

        s_retry_num = 0;
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}
/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// 只做最基础的 WiFi 初始化，用来扫描
static void wifi_init(void)
{
    //创建GroupEvent
    wifi_event_group = xEventGroupCreate();

    //初始化WiFi相关组件，包括网络接口和事件循环
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    //sta 创建
    esp_netif_t *sta_netif = esp_netif_create_default_wifi_sta();
    assert(sta_netif);

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();

    //注册wifi事件回调函数，监听wifi事件和ip事件
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL));

    //配置wifi连接信息
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };


    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    //set mac address 
    //ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CSI_RECV_MAC));


    //设置wifi连接配置
    ESP_ERROR_CHECK(esp_wifi_set_config(ESP_IF_WIFI_STA, &wifi_config) );

    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    printf("wifi_init_sta finished.\n");

    //只有等到失败或成功groupevent标志位置1后，才继续
    EventBits_t bits = xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT, pdFALSE, pdFALSE, portMAX_DELAY);

    //连接成功
    if (bits & WIFI_CONNECTED_BIT) {
        printf("connected to ap SSID:%s password:%s\n", WIFI_SSID, WIFI_PASS);
    }
    else if (bits & WIFI_FAIL_BIT) {
        printf("Failed to connect to SSID:%s, password:%s\n", WIFI_SSID, WIFI_PASS);
    }
    else {
        printf("UNEXPECTED EVENT\n");
    } 
}

/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// 先扫描附近 WiFi 并打印
static void wifi_scan_and_print(void)
{
    uint16_t ap_num = 0;
    uint16_t number = 0;

    memset(s_ap_records, 0, sizeof(s_ap_records));

    wifi_scan_config_t scan_config = {
        .ssid = NULL,
        .bssid = NULL,
        .channel = 0,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time = {
            .active = {
                .min = 100,
                .max = 300,
            }
        },
    };

    ESP_ERROR_CHECK(esp_wifi_scan_start(&scan_config, true));
    ESP_ERROR_CHECK(esp_wifi_scan_get_ap_num(&ap_num));

    number = ap_num;
    if (number > 20) {
        number = 20;
    }

    ESP_ERROR_CHECK(esp_wifi_scan_get_ap_records(&number, s_ap_records));

    printf("WiFi Scan Results:\n");

    for (int i = 0; i < number; i++) {
        printf("%3d | %7d | %4d | %s\n",
               i + 1,
               s_ap_records[i].primary,
               s_ap_records[i].rssi,
               (char *)s_ap_records[i].ssid);
    }
}


/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/

static void udp_init(void)
{
    s_udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_udp_sock < 0) {
        ESP_LOGE(TAG, "Unable to create UDP socket: errno %d", errno);
        return;
    }

    // 允许 UDP socket 发送广播包。
    // 如果不加 SO_BROADCAST，sendto() 发 255.255.255.255 可能会失败。
    int broadcast_enable = 1;
    int ret = setsockopt(s_udp_sock,
                         SOL_SOCKET,
                         SO_BROADCAST,
                         &broadcast_enable,
                         sizeof(broadcast_enable));
    if (ret < 0) {
        ESP_LOGE(TAG, "Failed to set SO_BROADCAST: errno %d", errno);
        close(s_udp_sock);
        s_udp_sock = -1;
        return;
    }

    memset(&s_udp_dest_addr, 0, sizeof(s_udp_dest_addr));
    s_udp_dest_addr.sin_family = AF_INET;
    s_udp_dest_addr.sin_port = htons(UDP_DEST_PORT);
    s_udp_dest_addr.sin_addr.s_addr = inet_addr(UDP_DEST_IP);

    ESP_LOGI(TAG, "UDP broadcast ready -> %s:%d", UDP_DEST_IP, UDP_DEST_PORT);
}

/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// 扫描完以后，再切到正式工作的固定配置
static void wifi_work_config_init(void)
{
    //连接wifi后不需要
    //ESP_ERROR_CHECK(esp_wifi_set_band_mode(WIFI_BAND_MODE_CFG));

    wifi_protocols_t protocols = {
        .ghz_2g = WIFI_PROTOCOL_11N,
        .ghz_5g = WIFI_PROTOCOL_5G_CFG,
    };
    //连接wifi后不需要
    //ESP_ERROR_CHECK(esp_wifi_set_protocols(WIFI_IF_STA, &protocols));

    wifi_bandwidths_t bandwidth = {
        .ghz_2g = WIFI_BW_HT20,
        .ghz_5g = WIFI_BANDWIDTH_5G_CFG,
    };
    //ESP_ERROR_CHECK(esp_wifi_set_bandwidths(WIFI_IF_STA, &bandwidth));

    //ESP_ERROR_CHECK(esp_wifi_set_channel(wifi_channel, WIFI_SECOND_CHAN_NONE));

    //ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CSI_SEND_MAC));
}

/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// ESP-NOW 初始化
static void wifi_esp_now_init(const esp_now_peer_info_t *peer)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));

    esp_now_rate_config_t rate_config = {
        .phymode = ESPNOW_PHYMODE_CFG,
        .rate    = ESPNOW_RATE_CFG,
        .ersu    = false,
        .dcm     = false,
    };

    ESP_ERROR_CHECK(esp_now_add_peer(peer));
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer->peer_addr, &rate_config));
}

/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/

// // CSI Power Info send callback



static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (info == NULL || info->buf == NULL) {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

    if (memcmp(info->mac, CSI_SEND_MAC, 6) != 0) {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    static int s_count = 0;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;

    float compensate_gain = 1.0f;

#if GAIN_CONTROL_ENABLE
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;

    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);

    if (s_count < 100) {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    } else if (s_count == 100) {
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);

    #if FORCE_GAIN_ENABLE
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
    #endif
    }

    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
#endif

    s_count++;

#if CSI_FORCE_LLTF
    return;
#else
    if (info->len < 2) {
        return;
    }

    // ----------------------------------------
    // 计算累计时间戳（单位 us）
    // 第一次记 0，之后每次累加与上次发送的间隔
    // ----------------------------------------
    static int64_t last_send_us = 0;
    static uint64_t elapsed_us = 0;

    int64_t now_us = esp_timer_get_time();

    if (last_send_us == 0) 
    {
        elapsed_us = 0;
    } 
    else 
    {
        int64_t delta_us = now_us - last_send_us;
        if (delta_us < 0) 
        {
            delta_us = 0;
        }

        elapsed_us += (uint64_t)(delta_us);
    }

    last_send_us = now_us;

    // ----------------------------------------
    // UDP发送 power 数据
    // 格式:
    // [uint64_t elapsed_us][uint16_t pair_count][uint32_t p0][uint32_t p1]...
    // 其中 p = I^2 + Q^2
    // ----------------------------------------
    uint16_t pair_count = info->len / 2;

    uint8_t udp_buf[1024];
    int udp_offset = 0;

    // 8字节 elapsed_us + 2字节 pair_count + 每个子载波功率 4字节
    int needed = 8 + 2 + pair_count * 4;
    if (needed > (int)sizeof(udp_buf)) {
        ESP_LOGW(TAG, "UDP power buffer too small, pair_count=%u", pair_count);
        return;
    }

    memcpy(udp_buf + udp_offset, &elapsed_us, sizeof(elapsed_us));
    udp_offset += sizeof(elapsed_us);

    memcpy(udp_buf + udp_offset, &pair_count, sizeof(pair_count));
    udp_offset += sizeof(pair_count);

    for (int i = 0; i + 1 < info->len; i += 2) {
        int8_t imag_raw = (int8_t)info->buf[i];
        int8_t real_raw = (int8_t)info->buf[i + 1];

        int16_t imag = (int16_t)(compensate_gain * imag_raw);
        int16_t real = (int16_t)(compensate_gain * real_raw);

        int32_t imag32 = (int32_t)imag;
        int32_t real32 = (int32_t)real;

        uint32_t power = (uint32_t)(imag32 * imag32 + real32 * real32);

        memcpy(udp_buf + udp_offset, &power, sizeof(power));
        udp_offset += sizeof(power);
    }

    if (s_udp_sock >= 0) {
        int err = sendto(s_udp_sock,
                         udp_buf,
                         udp_offset,
                         0,
                         (struct sockaddr *)&s_udp_dest_addr,
                         sizeof(s_udp_dest_addr));

        if (err < 0) {
            ESP_LOGW(TAG, "UDP send failed: errno %d", errno);
        }
    }

    // ----------------------------------------
    // 串口调试打印（默认注释）
    // 打印形式:
    // t=1234,[p0,p1,p2,...]
    // ----------------------------------------
    /*
    char out_buf[1500];
    int offset = 0;

    offset += snprintf(out_buf + offset, sizeof(out_buf) - offset,
                       "t=%llu,[", (unsigned long long)elapsed_us);

    if (offset >= 0 && offset < (int)sizeof(out_buf)) {
        for (int i = 0; i + 1 < info->len; i += 2) {
            int8_t imag_raw = (int8_t)info->buf[i];
            int8_t real_raw = (int8_t)info->buf[i + 1];

            int16_t imag = (int16_t)(compensate_gain * imag_raw);
            int16_t real = (int16_t)(compensate_gain * real_raw);

            int32_t imag32 = (int32_t)imag;
            int32_t real32 = (int32_t)real;

            uint32_t power = (uint32_t)(imag32 * imag32 + real32 * real32);

            if (i == 0) {
                offset += snprintf(out_buf + offset, sizeof(out_buf) - offset,
                                   "%lu", (unsigned long)power);
            } else {
                offset += snprintf(out_buf + offset, sizeof(out_buf) - offset,
                                   ",%lu", (unsigned long)power);
            }

            if (offset < 0 || offset >= (int)sizeof(out_buf) - 16) {
                break;
            }
        }

        if (offset >= 0 && offset < (int)sizeof(out_buf) - 3) {
            offset += snprintf(out_buf + offset, sizeof(out_buf) - offset, "]\n");
        } else {
            out_buf[sizeof(out_buf) - 2] = '\n';
            out_buf[sizeof(out_buf) - 1] = '\0';
        }

        if ((s_count % 2) == 0) {
            ets_printf("%s", out_buf);
        }
    }
    */

#endif
}

/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// // CSI Raw IQ info send callback
// static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
// {
//     if (info == NULL || info->buf == NULL) {
//         ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
//         return;
//     }

//     if (memcmp(info->mac, CSI_SEND_MAC, 6) != 0) {
//         return;
//     }

//     const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

//     static int s_count = 0;
//     static uint8_t agc_gain = 0;
//     static int8_t fft_gain = 0;

//     float compensate_gain = 1.0f;

// #if GAIN_CONTROL_ENABLE
//     static uint8_t agc_gain_baseline = 0;
//     static int8_t fft_gain_baseline = 0;

//     esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);

//     if (s_count < 100) {
//         esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
//     } else if (s_count == 100) {
//         esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);

//     #if FORCE_GAIN_ENABLE
//         esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
//         ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
//     #endif
//     }

//     esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
// #endif

//     s_count++;

// #if CSI_FORCE_LLTF
//     return;
// #else
//     if (info->len < 2) {
//         return;
//     }

//     // ----------------------------------------
//     // 计算累计时间戳（单位 ms）
//     // 第一次记 0，之后每次累加与上次发送的间隔
//     // ----------------------------------------
//     static int64_t last_send_us = 0;
//     static uint32_t elapsed_ms = 0;

//     int64_t now_us = esp_timer_get_time();

//     if (last_send_us == 0) {
//         elapsed_ms = 0;
//     } else {
//         int64_t delta_us = now_us - last_send_us;
//         if (delta_us < 0) {
//             delta_us = 0;
//         }
//         elapsed_ms += (uint32_t)(delta_us / 1000);
//     }

//     last_send_us = now_us;

//     // ----------------------------------------
//     // UDP发送 原始 IQ 数据 + 时间戳
//     // 格式:
//     // [uint32_t elapsed_ms][uint16_t pair_count]
//     // [int16_t imag0][int16_t real0][int16_t imag1][int16_t real1]...
//     //
//     // 每个子载波占 4 字节:
//     //   2字节 Imag + 2字节 Real
//     // ----------------------------------------
//     uint16_t pair_count = info->len / 2;

//     uint8_t udp_buf[1024];
//     int udp_offset = 0;

//     // 4字节 elapsed_ms + 2字节 pair_count + 每个子载波(I/Q)4字节
//     int needed = 4 + 2 + pair_count * 4;
//     if (needed > (int)sizeof(udp_buf)) {
//         ESP_LOGW(TAG, "UDP IQ buffer too small, pair_count=%u", pair_count);
//         return;
//     }

//     memcpy(udp_buf + udp_offset, &elapsed_ms, sizeof(elapsed_ms));
//     udp_offset += sizeof(elapsed_ms);

//     memcpy(udp_buf + udp_offset, &pair_count, sizeof(pair_count));
//     udp_offset += sizeof(pair_count);

//     for (int i = 0; i + 1 < info->len; i += 2) {
//         int8_t imag_raw = (int8_t)info->buf[i];
//         int8_t real_raw = (int8_t)info->buf[i + 1];

//         // 保留 gain 补偿
//         int16_t imag = (int16_t)(compensate_gain * imag_raw);
//         int16_t real = (int16_t)(compensate_gain * real_raw);

//         memcpy(udp_buf + udp_offset, &imag, sizeof(imag));
//         udp_offset += sizeof(imag);

//         memcpy(udp_buf + udp_offset, &real, sizeof(real));
//         udp_offset += sizeof(real);
//     }

//     if (s_udp_sock >= 0) {
//         int err = sendto(s_udp_sock,
//                          udp_buf,
//                          udp_offset,
//                          0,
//                          (struct sockaddr *)&s_udp_dest_addr,
//                          sizeof(s_udp_dest_addr));

//         if (err < 0) {
//             ESP_LOGW(TAG, "UDP send failed: errno %d", errno);
//         }
//     }
// #endif
// }



/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// CSI 初始化
static void wifi_csi_init(void)
{
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    wifi_csi_config_t csi_config = {
        .enable                   = true,
        .acquire_csi_legacy       = false,
        .acquire_csi_force_lltf   = CSI_FORCE_LLTF,
        .acquire_csi_ht20         = true,
        .acquire_csi_ht40         = false,
        .acquire_csi_vht          = false,
        .acquire_csi_su           = false,
        .acquire_csi_mu           = false,
        .acquire_csi_dcm          = false,
        .acquire_csi_beamformed   = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg            = 0,
        .dump_ack_en              = false,
        .reserved                 = false,
    };

    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

/*
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
********************************************************************************************************************************
*/
// main
void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // 第一步：只初始化基础 WiFi，用于扫描
    wifi_init();

    // // 第二步：先扫描并打印附近 WiFi
    // wifi_scan_and_print();

    // 第三步：扫描结束后，切到正式工作配置
    wifi_work_config_init();

    // 第四步：初始化 ESP-NOW
    esp_now_peer_info_t peer = {
        .channel   = wifi_channel,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    wifi_esp_now_init(&peer);

    // 第五步：初始化 UDP
    udp_init();
    
    // 第五步：开启 CSI
    wifi_csi_init();
}
