# How to run OptiSigns AI Add-on on start up in Windows

Article URL: https://support.optisigns.com/hc/en-us/articles/360058345334-How-to-run-OptiSigns-AI-Add-on-on-start-up-in-Windows

In Windows, you can use Windows' Task Scheduler to set up OptiSigns AI Add-on to run on start up.

It only take a few simple steps

Click **Start** -> Type "**Task Scheduler**" and open it

Then click **Create Basic task**

![mceclip0.png](https://support.optisigns.com/hc/article_attachments/360100967213)

Give it a name (e.g. OptiSigns AI Add-on Autostart)

Then click **Next**

![mceclip1.png](https://support.optisigns.com/hc/article_attachments/360098751914)

Next in Trigger section select "**When I Log on**"\
Please do not select other event like When computer start, it may not work.

![mceclip2.png](https://support.optisigns.com/hc/article_attachments/360100967293)

Next in Action section select "**Start a program**"

![mceclip3.png](https://support.optisigns.com/hc/article_attachments/360098752054)

Click Browse and browse to optisigns-ai-detection.exe that you've downloaded.

![mceclip4.png](https://support.optisigns.com/hc/article_attachments/360098752114)

Then click **Finish**

![mceclip5.png](https://support.optisigns.com/hc/article_attachments/360098752154)

## Related Articles

- [AI Camera App - Quick Start](https://support.optisigns.com/hc/en-us/articles/27690296225555?ref=related)
- [How to manually install & activate OptiSigns AI Add-on on Linux or Raspberry Pi](https://support.optisigns.com/hc/en-us/articles/360060676993?ref=related)
- [Audience Intelligence (AI Camera) FAQs](https://support.optisigns.com/hc/en-us/articles/360058259834?ref=related)
- [Windows](https://support.optisigns.com/hc/en-us/articles/360034379293?ref=related)

That's all!

Next time your PC start up, it will start OptiSigns AI Detection app, doing it this way will also ensure that it will get started before the OptiSigns Digital Signage Player app start.
