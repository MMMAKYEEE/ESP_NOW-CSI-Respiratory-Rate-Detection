/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* Get Start Example

   This example code is in the Public Domain (or CC0 licensed, at your option.)

   Unless required by applicable law or agreed to in writing, this
   software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
   CONDITIONS OF ANY KIND, either express or implied.
*/

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "nvs_flash.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"


//wifi information
#define WIFI_SSID "WiFi-7425-5G"
#define WIFI_PASS "97322115"


#define WIFI_CHANNEL                36 //////////////////////////////////
#define WIFI_BAND_MODE_CFG          WIFI_BAND_MODE_5G_ONLY //////////////////////////////////
#define WIFI_BANDWIDTH_5G_CFG       WIFI_BW_HT20 //////////////////////////////////
#define WIFI_PROTOCOL_5G_CFG        (WIFI_PROTOCOL_11A | WIFI_PROTOCOL_11N) //////////////////////////////////

#define ESPNOW_PHYMODE_CFG          WIFI_PHY_MODE_HT20 //////////////////////////////////
#define ESPNOW_RATE_CFG             WIFI_PHY_RATE_MCS0_LGI
#define SEND_FREQUENCY              100

static const uint8_t CSI_SEND_MAC[] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};
static const char *TAG = "csi_send_5g_ht20"; //////////////////////////////////

uint16_t wifi_channel = 0;

//GroupEvent 标志位，表示连接成功或失败
static EventGroupHandle_t wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1


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
        wifi_event_sta_disconnected_t *disconn = (wifi_event_sta_disconnected_t *)event_data;
        ESP_LOGW(TAG, "WiFi disconnected, reason = %d", disconn->reason);

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
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CSI_SEND_MAC));


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



static void wifi_esp_now_init(const esp_now_peer_info_t *peer)
{
    ESP_ERROR_CHECK(esp_now_init()); //初始化espnow
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));//设置primary key
    ESP_ERROR_CHECK(esp_now_add_peer(peer)); //设置通信对端(PEER), 0xff, 0xff, 0xff, 0xff, 0xff, 0xff 意思为发送给所有设备(广播)

    esp_now_rate_config_t rate_config = {
        .phymode = ESPNOW_PHYMODE_CFG,
        .rate    = ESPNOW_RATE_CFG,
        .ersu    = false,
        .dcm     = false
    };

    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer->peer_addr, &rate_config));
}


void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init();

    esp_now_peer_info_t peer = {
        .channel   = wifi_channel, //
        .ifidx     = WIFI_IF_STA, //sta模式通信
        .encrypt   = false,       //不加密
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff}, //广播给所有设备，如果指定设备，这写接收端mac
    };
    wifi_esp_now_init(&peer);

    ESP_LOGI(TAG, "================ CSI SEND 5G HT20 ================"); //////////////////////////////////
    ESP_LOGI(TAG, "wifi_channel: %d, send_frequency: %d, mac: " MACSTR,
             wifi_channel, SEND_FREQUENCY, MAC2STR(CSI_SEND_MAC));

    
    //循环发送内容
    for (uint32_t count = 0; ; ++count) {
        esp_err_t ret = esp_now_send(peer.peer_addr, (const uint8_t *)&count, sizeof(count)); //发送内容为count值
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "free_heap: %ld <%s> ESP-NOW send error",
                     esp_get_free_heap_size(), esp_err_to_name(ret));
        }

        usleep(1000 * 1000 / SEND_FREQUENCY);
    }
}