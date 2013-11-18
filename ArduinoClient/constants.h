//hard coded value
#define MAX_MOTOR_CURRENT 1500

//register
#define GENERAL_CONFIG_REGISTER 0x0
#define START_CONFIG_REGISTER 0x2
#define SPIOUT_CONF_REGISTER 0x04
#define STEP_CONF_REGISTER 0x0A
#define EVENT_CLEAR_CONF_REGISTER 0x0c
#define INTERRUPT_REGISTER 0x0d
#define EVENTS_REGISTER 0x0e
#define STATUS_REGISTER 0x0f
#define START_OUT_ADD_REGISTER 0x11
#define GEAR_RATIO_REGISTER 0x12
#define START_DELAY_REGISTER 0x13
#define RAMP_MODE_REGISTER 0x20
#define X_ACTUAL_REGISTER 0x21
#define V_ACTUAL_REGISTER 0x22
#define V_MAX_REGISTER 0x24
#define V_START_REGISTER 0x25
#define V_STOP_REGISTER 0x26
#define A_MAX_REGISTER 0x28
#define D_MAX_REGISTER 0x29
#define BOW_1_REGISTER 0x2d
#define BOW_2_REGISTER 0x2e
#define BOW_3_REGISTER 0x2f
#define BOW_4_REGISTER 0x30
#define CLK_FREQ_REGISTER 0x31
#define X_TARGET_REGISTER 0x37
#define X_TARGET_PIPE_0_REGSISTER 0x38
#define SH_RAMP_MODE_REGISTER 0x40
#define SH_V_MAX_REGISTER 0x41
#define SH_V_START_REGISTER 0x42
#define SH_V_STOP_REGISTER 0x43
#define SH_VBREAK_REGISTER 0x44
#define SH_A_MAX_REGISTER 0x45
#define SH_D_MAX_REGISTER 0x46
#define SH_BOW_1_REGISTER 0x49
#define SH_BOW_2_REGISTER 0x4a
#define SH_BOW_3_REGISTER 0x4b
#define SH_BOW_4_REGISTER 0x4c
#define COVER_LOW_REGISTER 0x6c
#define COVER_HIGH_REGISTER 0x6d

//some nice calculation s
//simple FP math see https://ucexperiment.wordpress.com/2012/10/28/fixed-point-math-on-the-arduino-platform/
#define FIXED_8_24_MAKE(a)     (int32_t)((a*(1ul << 24ul)))
#define FIXED_24_8_MAKE(a)     (int32_t)((a*(1ul << 8ul)))

